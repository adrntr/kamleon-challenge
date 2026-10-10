"""RFC 9457 problem details: error responses and the only place errors are logged."""

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.errors import (
    BatchRejectedError,
    CustomerNotFoundError,
    DeviceInactiveError,
    DeviceNotFoundError,
    EmailAlreadyRegisteredError,
    SerialAlreadyRegisteredError,
)
from app.errors import AppError, DatabaseUnavailableError
from app.schemas.measurement import MeasurementBatchRead
from app.schemas.problem import PROBLEM_TYPE_BASE, ProblemDetails

logger = logging.getLogger("app.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"
# For `responses=` in routes. The schema is inlined because FastAPI's "model" key
# documents it under application/json instead of application/problem+json.
PROBLEM_RESPONSE: dict[str, Any] = {
    "description": "Problem details (RFC 9457)",
    "content": {PROBLEM_MEDIA_TYPE: {"schema": ProblemDetails.model_json_schema()}},
}


@dataclass(frozen=True)
class ProblemType:
    status: int
    slug: str
    title: str


VALIDATION_ERROR = ProblemType(422, "validation-error", "Request validation failed")
INTERNAL_ERROR = ProblemType(500, "internal-error", "Internal server error")

# Errors know nothing about HTTP: statuses and problem types are assigned here.
APP_PROBLEMS: dict[type[AppError], ProblemType] = {
    DatabaseUnavailableError: ProblemType(503, "service-unavailable", "Service unavailable"),
    CustomerNotFoundError: ProblemType(404, "customer-not-found", "Customer not found"),
    EmailAlreadyRegisteredError: ProblemType(
        409, "email-already-registered", "Email already registered"
    ),
    DeviceNotFoundError: ProblemType(404, "device-not-found", "Device not found"),
    SerialAlreadyRegisteredError: ProblemType(
        409, "serial-already-registered", "Serial number already registered"
    ),
    DeviceInactiveError: ProblemType(409, "device-inactive", "Device is inactive"),
    BatchRejectedError: ProblemType(
        422, "batch-rejected", "All measurements in the batch were rejected"
    ),
}


def problem_response(
    problem: ProblemType,
    instance: str,
    *,
    detail: str | None = None,
    extensions: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_BASE + problem.slug,
        "title": problem.title,
        "status": problem.status,
    }
    if detail is not None:
        body["detail"] = detail
    body["instance"] = instance
    body |= extensions or {}
    return JSONResponse(
        body, status_code=problem.status, headers=headers, media_type=PROBLEM_MEDIA_TYPE
    )


def _describe_causes(exc: BaseException) -> str:
    """The cause chain in one line: class and first line of each message.

    Only the first line: driver messages continue with DETAIL lines that can
    contain row values.
    """
    causes = []
    cause = exc.__cause__
    while cause is not None:
        first_line = next(iter(str(cause).splitlines()), "")
        causes.append(f"{type(cause).__name__}: {first_line}")
        cause = cause.__cause__
    return " <- ".join(causes)


def _log_client_error(request: Request, problem: ProblemType) -> None:
    """A 4xx in one line: problem type, status, method and path only.

    Never the detail: it is written for the client and can contain personal
    data (e.g. the email in email-already-registered).
    """
    logger.warning("%s %s -> %d %s", request.method, request.url.path, problem.status, problem.slug)


def _log_one_line(request: Request, problem: ProblemType, summary: str, exc: Exception) -> None:
    causes = _describe_causes(exc)
    logger.warning(
        "%s %s -> %d %s: %s%s",
        request.method,
        request.url.path,
        problem.status,
        problem.slug,
        summary,
        f" (caused by {causes})" if causes else "",
    )


def _app_problem(exc: AppError) -> ProblemType | None:
    for cls in type(exc).__mro__:
        if cls in APP_PROBLEMS:
            return APP_PROBLEMS[cls]
    return None


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    problem = _app_problem(exc)
    if problem is None:
        # An error without a mapping is a bug: let it become a 500.
        raise exc
    if problem.status < 500:
        _log_client_error(request, problem)
    elif isinstance(exc, DatabaseUnavailableError):
        # The readiness 503 is expected during outages: one line, no stack trace
        # (AGENTS.md, rule 5).
        _log_one_line(request, problem, exc.detail, exc)
    else:
        logger.error(
            "%s %s -> %d %s: %s",
            request.method,
            request.url.path,
            problem.status,
            problem.slug,
            exc.detail,
            exc_info=exc,
        )
    return problem_response(
        problem, request.url.path, detail=exc.detail, extensions=_app_extensions(exc)
    )


def _app_extensions(exc: AppError) -> dict[str, Any] | None:
    if isinstance(exc, BatchRejectedError):
        # The same summary and results as a 201/207 body (DECISIONS.md, Batches).
        return MeasurementBatchRead.from_result(exc.result).model_dump(
            mode="json", exclude_none=True
        )
    return None


def _escape_pointer_token(token: Any) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _error_location(error: dict[str, Any]) -> dict[str, str]:
    """JSON Pointer for body errors, parameter or header name otherwise."""
    source, *path = error["loc"]
    if source == "body":
        if error["type"] == "json_invalid":
            path = []  # the location is a character offset, not a field
        return {"pointer": "#" + "".join(f"/{_escape_pointer_token(token)}" for token in path)}
    if source == "header":
        return {"header": str(path[0])}
    return {"parameter": str(path[0]) if path else str(source)}


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # pydantic's "input" is never copied: it can be health data (AGENTS.md, rule 5).
    errors = [_error_location(error) | {"detail": error["msg"]} for error in exc.errors()]
    _log_client_error(request, VALIDATION_ERROR)
    return problem_response(VALIDATION_ERROR, request.url.path, extensions={"errors": errors})


async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Route-level errors raised by Starlette (unknown path → 404, wrong method → 405)."""
    phrase = HTTPStatus(exc.status_code).phrase
    problem = ProblemType(exc.status_code, phrase.lower().replace(" ", "-"), phrase)
    detail = exc.detail if exc.detail != phrase else None
    _log_client_error(request, problem)
    return problem_response(problem, request.url.path, detail=detail, headers=exc.headers)


class UnhandledErrorMiddleware:
    """Turns unexpected exceptions into a 500 problem and logs them once, with stack trace.

    Not an exception handler for Exception: Starlette re-raises after calling
    it, so the server would log the same error a second time.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracking_start(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking_start)
        except Exception:
            if response_started:
                # Too late to send a problem response; the server closes the connection.
                raise
            logger.exception("%s %s -> 500 %s", scope["method"], scope["path"], INTERNAL_ERROR.slug)
            response = problem_response(
                INTERNAL_ERROR, scope["path"], detail="An unexpected error occurred"
            )
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(HTTPException, handle_http_exception)

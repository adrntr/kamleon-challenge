"""Errors that any layer may raise and the API reports as problem details.

Business errors subclass DomainError (app/domain/errors.py). Infrastructure
errors that must cross layers, such as an unreachable database, live here: they
are raised by repositories but are not business rules (DECISIONS.md, Architecture).
"""


class AppError(Exception):
    """Base class for errors the API reports to clients.

    Errors carry only a human-readable detail; the HTTP status and the problem
    type are assigned in the API layer (app/api/problems.py).
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class DatabaseUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__("The database is not reachable")

from pydantic import BaseModel, ConfigDict

PROBLEM_TYPE_BASE = "https://api.kamleon.example/problems/"


class ProblemDetails(BaseModel):
    """RFC 9457 problem details. Used only to document error responses in OpenAPI."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str

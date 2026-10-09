import uuid
from typing import Any

from fastapi import APIRouter, Response

from app.api.dependencies import SessionDep
from app.api.problems import PROBLEM_MEDIA_TYPE, PROBLEM_RESPONSE
from app.domain.measurements import ItemStatus, MeasurementPayload
from app.schemas.measurement import (
    BatchRejectedProblem,
    MeasurementBatchCreate,
    MeasurementBatchRead,
)
from app.schemas.problem import ProblemDetails
from app.services import measurements as measurement_service

router = APIRouter(prefix="/devices/{device_id}/measurements", tags=["measurements"])


def _component_refs_schema(model: type[BatchRejectedProblem]) -> dict[str, Any]:
    """Inline schema whose nested models point to the components of the 201 response model."""
    schema = model.model_json_schema(
        ref_template="#/components/schemas/{model}", mode="serialization"
    )
    schema.pop("$defs", None)
    return schema


@router.post(
    "",
    status_code=201,
    response_model_exclude_none=True,
    responses={
        201: {"description": "Every measurement was created or is a duplicate (a retry)."},
        207: {
            "model": MeasurementBatchRead,
            "description": "Mixed result: some measurements were rejected, see results.",
        },
        404: {**PROBLEM_RESPONSE, "description": "Device not found (device-not-found)"},
        409: {**PROBLEM_RESPONSE, "description": "Device is inactive (device-inactive)"},
        422: {
            "description": (
                "Every measurement was rejected (batch-rejected, with summary and results), "
                "or the body, path or id is invalid (validation-error)."
            ),
            "content": {
                PROBLEM_MEDIA_TYPE: {
                    "schema": {
                        "anyOf": [
                            _component_refs_schema(BatchRejectedProblem),
                            ProblemDetails.model_json_schema(),
                        ]
                    }
                }
            },
        },
    },
)
async def ingest_measurements(
    device_id: uuid.UUID, body: MeasurementBatchCreate, session: SessionDep, response: Response
) -> MeasurementBatchRead:
    """Ingest a batch of measurements. Retries of stored measurements are reported as duplicate."""
    result = await measurement_service.ingest_measurements(
        session,
        device_id,
        [MeasurementPayload(**measurement.model_dump()) for measurement in body.measurements],
    )
    # All rejected raises BatchRejectedError, so any rejection here means a mixed result.
    if result.count(ItemStatus.REJECTED):
        response.status_code = 207
    return MeasurementBatchRead.from_result(result)

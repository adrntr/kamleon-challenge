import uuid

from fastapi import APIRouter, Response

from app.api.dependencies import SessionDep
from app.api.problems import PROBLEM_RESPONSE
from app.schemas.customer import CustomerCreate, CustomerRead
from app.services import customers as customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post(
    "",
    status_code=201,
    responses={
        201: {
            "headers": {
                "Location": {
                    "description": "URL of the created customer",
                    "schema": {"type": "string"},
                }
            }
        },
        409: {
            **PROBLEM_RESPONSE,
            "description": "Email already registered (email-already-registered)",
        },
        422: {**PROBLEM_RESPONSE, "description": "Invalid body (validation-error)"},
    },
)
async def create_customer(
    body: CustomerCreate, session: SessionDep, response: Response
) -> CustomerRead:
    """Register a customer. The email is lowercased before storing."""
    customer = await customer_service.register_customer(session, name=body.name, email=body.email)
    response.headers["Location"] = f"/customers/{customer.id}"
    return CustomerRead.model_validate(customer)


@router.get(
    "/{customer_id}",
    responses={
        404: {**PROBLEM_RESPONSE, "description": "Customer not found (customer-not-found)"},
        422: {**PROBLEM_RESPONSE, "description": "Malformed customer id (validation-error)"},
    },
)
async def get_customer(customer_id: uuid.UUID, session: SessionDep) -> CustomerRead:
    """Retrieve a customer by id."""
    customer = await customer_service.get_customer(session, customer_id)
    return CustomerRead.model_validate(customer)

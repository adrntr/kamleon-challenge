class DomainError(Exception):
    """Base class for errors the API reports to clients.

    Domain errors carry only a human-readable detail; the HTTP status and the
    problem type are assigned in the API layer (app/api/problems.py).
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class DatabaseUnavailableError(DomainError):
    def __init__(self) -> None:
        super().__init__("The database is not reachable")


class CustomerNotFoundError(DomainError):
    def __init__(self, customer_id: object) -> None:
        super().__init__(f"Customer {customer_id} does not exist")


class EmailAlreadyRegisteredError(DomainError):
    def __init__(self, email: str) -> None:
        super().__init__(f"A customer with email {email} is already registered")

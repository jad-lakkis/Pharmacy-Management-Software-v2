"""Domain exceptions. The API layer maps each to an HTTP status code."""


class PharmacyError(Exception):
    """Base class for all expected, user-facing errors."""

    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(PharmacyError):
    """Input is malformed or breaks a business rule."""

    status = 400


class AuthenticationError(PharmacyError):
    """Credentials or session are missing or invalid."""

    status = 401


class PermissionDeniedError(PharmacyError):
    """The acting user lacks the permission for this action."""

    status = 403


class NotFoundError(PharmacyError):
    """The entity does not exist (or is not visible to the actor)."""

    status = 404


class InvalidTransitionError(PharmacyError):
    """A workflow transition is not allowed from the current state."""

    status = 409


class ConflictError(PharmacyError):
    """The action conflicts with existing data (duplicates, double payment...)."""

    status = 409

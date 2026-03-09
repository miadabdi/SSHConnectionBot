class DomainError(Exception):
    """Base domain-level exception."""


class NotFoundError(DomainError):
    """Raised when an entity cannot be found."""


class ValidationError(DomainError):
    """Raised on invalid user input."""


class SessionUnavailableError(DomainError):
    """Raised when no active session is available."""

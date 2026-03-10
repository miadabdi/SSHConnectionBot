class DomainError(Exception):
    """Base domain-level exception."""


class NotFoundError(DomainError):
    """Raised when an entity cannot be found."""


class ValidationError(DomainError):
    """Raised on invalid user input."""


class SessionUnavailableError(DomainError):
    """Raised when no active session is available."""


class InteractiveInputRequiredError(DomainError):
    """Raised when a running shell command requests additional input."""

    def __init__(self, prompt: str, partial_output: str = "") -> None:
        super().__init__(prompt)
        self.prompt = prompt
        self.partial_output = partial_output

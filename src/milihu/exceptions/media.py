"""Media backend domain exceptions."""


class MediaBackendError(RuntimeError):
    """Raised when a media backend request fails at the transport or HTTP layer."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

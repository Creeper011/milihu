"""Payload and size-limit exceptions."""

from milihu.types.default.payload import BYTES_PER_MIB


class PayloadTooLargeError(Exception):
    """Raised when a generated payload exceeds the target byte budget."""

    def __init__(
        self,
        max_bytes: int | None = None,
        *,
        size: int | None = None,
    ) -> None:
        self.size = size
        self.max_bytes = max_bytes
        if size is not None and max_bytes is not None:
            message = (
                "The generated file is too large "
                f"(`{size / BYTES_PER_MIB:.1f} MiB` > "
                f"`{max_bytes / BYTES_PER_MIB:.1f} MiB` limit). "
                "Try a shorter or smaller input."
            )
        elif max_bytes is not None:
            message = (
                "The generated file exceeds the "
                f"`{max_bytes / BYTES_PER_MIB:.1f} MiB` limit. "
                "Try a shorter or smaller input."
            )
        else:
            message = (
                "The generated file exceeds the upload size limit. "
                "Try a shorter or smaller input."
            )
        super().__init__(message)


class InputTooLargeError(PayloadTooLargeError):
    """Raised when an input media file exceeds the maximum allowed byte size."""

    def __init__(self, size: int, max_bytes: int) -> None:
        self.size = size
        self.max_bytes = max_bytes
        Exception.__init__(
            self,
            "The input file is too large "
            f"(`{size / BYTES_PER_MIB:.1f} MiB` > "
            f"`{max_bytes / BYTES_PER_MIB:.1f} MiB` limit).",
        )

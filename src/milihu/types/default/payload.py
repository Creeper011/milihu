"""Default payload size constants defined with Pydantic."""

from typing import Annotated

from pydantic import Field

ByteSize = Annotated[int, Field(gt=0)]

DEFAULT_PAYLOAD_OVERHEAD_BYTES: ByteSize = 64 * 1024
BYTES_PER_MIB: ByteSize = 1024 * 1024
DEFAULT_FILE_SIZE_LIMIT_BYTES: ByteSize = 20 * BYTES_PER_MIB

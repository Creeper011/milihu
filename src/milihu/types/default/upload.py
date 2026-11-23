"""Default temporary upload constants."""

from milihu.types.default.payload import BYTES_PER_MIB, ByteSize
from milihu.types.upload import UploadLifetime

DEFAULT_UPLOAD_LIFETIME: UploadLifetime = UploadLifetime.TWELVE_HOURS
DEFAULT_MAX_UPLOAD_BYTES: ByteSize = 1024 * BYTES_PER_MIB

LITTERBOX_API_URL = "https://litterbox.catbox.moe/resources/internals/api.php"

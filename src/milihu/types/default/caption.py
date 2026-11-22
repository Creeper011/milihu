"""Default caption configuration constants."""

from pydantic import PositiveFloat, PositiveInt

from milihu.types.caption import CaptionPosition, CaptionStyle
from milihu.types.default.gif import DEFAULT_GIF_PRESETS, GifPreset
from milihu.types.default.payload import BYTES_PER_MIB, ByteSize

DEFAULT_CAPTION_POSITION: CaptionPosition = "top"
DEFAULT_CAPTION_STYLE: CaptionStyle = "bar"
DEFAULT_CAPTION_MAX_TEXT_LENGTH: PositiveInt = 200
DEFAULT_CAPTION_WRAP_WIDTH: PositiveInt = 28
DEFAULT_CAPTION_SIZE_POLL_SECONDS: PositiveFloat = 0.2
DEFAULT_MAX_CAPTION_INPUT_BYTES: ByteSize = 100 * BYTES_PER_MIB
DEFAULT_CAPTION_PRESETS: tuple[GifPreset, ...] = DEFAULT_GIF_PRESETS

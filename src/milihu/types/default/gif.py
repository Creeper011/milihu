"""Default GIF encoding presets defined with Pydantic."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from milihu.types.default.payload import BYTES_PER_MIB, ByteSize


class GifPreset(BaseModel):
    """Quality preset for progressive GIF encoding."""

    model_config = ConfigDict(frozen=True)

    fps: PositiveInt
    width: PositiveInt
    max_colors: Annotated[int, Field(ge=2, le=256)]


DEFAULT_GIF_PRESETS: tuple[GifPreset, ...] = (
    GifPreset(fps=20, width=640, max_colors=256),
    GifPreset(fps=18, width=540, max_colors=256),
    GifPreset(fps=15, width=480, max_colors=256),
    GifPreset(fps=12, width=360, max_colors=128),
    GifPreset(fps=10, width=320, max_colors=96),
    GifPreset(fps=8, width=240, max_colors=64),
    GifPreset(fps=6, width=200, max_colors=48),
)

DEFAULT_SHORT_MEDIA_SECONDS: PositiveFloat = 10.0
DEFAULT_LONG_MEDIA_SECONDS: PositiveFloat = 60.0
DEFAULT_GIF_SIZE_POLL_SECONDS: PositiveFloat = 0.2
DEFAULT_MAX_GIF_INPUT_BYTES: ByteSize = 100 * BYTES_PER_MIB

"""Caption type definitions."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from milihu.types.common import NonEmptyStr

CaptionPosition = Literal["top", "bottom"]
CaptionStyle = Literal["bar", "overlay"]
CaptionOutputExtension = Literal[".png", ".gif"]

CAPTION_POSITIONS: tuple[CaptionPosition, ...] = get_args(CaptionPosition)
CAPTION_STYLES: tuple[CaptionStyle, ...] = get_args(CaptionStyle)


class CaptionOutput(BaseModel):
    """Rendered captioned media payload and output metadata."""

    model_config = ConfigDict(frozen=True)

    filename: NonEmptyStr
    data: bytes
    extension: CaptionOutputExtension

"""Downloaded file and its transfer metrics."""

from pathlib import PurePath
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    NonNegativeFloat,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from milihu.types.common import NonEmptyStr

Resolution = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^(\d{2,5}x\d{2,5}|\d{3,4}p)$",
    ),
]
Codec = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=r"^(h264|av1|vp9|hevc|h265|aac|opus|mp3|ogg|vorbis|wav|flac)$",
    ),
]

_RESOLUTION_ADAPTER: TypeAdapter[Resolution] = TypeAdapter(Resolution)
_CODEC_ADAPTER: TypeAdapter[Codec] = TypeAdapter(Codec)


class DownloadOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: NonEmptyStr
    data: bytes
    elapsed_seconds: NonNegativeFloat
    url: NonEmptyStr | None = None
    resolution: Resolution | None = None
    codec: Codec | None = None

    @model_validator(mode="after")
    def _extract_filename_metadata(self) -> Self:
        need_resolution = self.resolution is None
        need_codec = self.codec is None
        if not (need_resolution or need_codec):
            return self
        for token in PurePath(self.filename).stem.split("_"):
            if need_resolution:
                try:
                    object.__setattr__(
                        self,
                        "resolution",
                        _RESOLUTION_ADAPTER.validate_python(token),
                    )
                    need_resolution = False
                    continue
                except ValidationError:
                    pass
            if need_codec:
                try:
                    object.__setattr__(
                        self,
                        "codec",
                        _CODEC_ADAPTER.validate_python(token),
                    )
                    need_codec = False
                except ValidationError:
                    pass
        return self

    @property
    def size_bytes(self) -> int:
        return len(self.data)

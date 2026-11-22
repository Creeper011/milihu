"""Validated input for a media download."""

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.download.format import DownloadFormat
from milihu.types.download.quality import DownloadQuality


class DownloadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    format: DownloadFormat | None = None
    quality: DownloadQuality | None = None

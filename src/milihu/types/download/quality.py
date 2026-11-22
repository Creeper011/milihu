"""Video qualities available for downloads."""

from enum import StrEnum


class DownloadQuality(StrEnum):
    Q1440 = "1440"
    Q1080 = "1080"
    Q720 = "720"
    Q480 = "480"
    Q360 = "360"
    Q240 = "240"
    Q144 = "144"


QUALITY_LADDER: tuple[DownloadQuality, ...] = tuple(DownloadQuality)

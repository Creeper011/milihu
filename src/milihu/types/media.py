"""Media type definitions."""

from typing import Literal, NotRequired, TypedDict, get_args

from milihu.types.download.format import DownloadFormat
from milihu.types.download.quality import DownloadQuality

MediaPrefix = Literal["image/", "video/"]

MediaExtension = Literal[
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
]

MEDIA_PREFIXES: tuple[str, ...] = get_args(MediaPrefix)
MEDIA_EXTENSIONS: frozenset[str] = frozenset(get_args(MediaExtension))


class MediaResolveOptions(TypedDict, total=False):
    format: DownloadFormat
    mode: Literal["auto", "audio", "mute"]
    video_quality: DownloadQuality


class MediaFile(TypedDict):
    status: Literal["file"]
    url: str
    filename: str
    delivery: Literal["direct", "proxy"]


class MediaItem(TypedDict):
    type: Literal["photo", "video", "gif"]
    url: str
    thumb: NotRequired[str]


class MediaSelection(TypedDict):
    status: Literal["selection"]
    items: list[MediaItem]
    audio: NotRequired[str]
    audio_filename: NotRequired[str]


class MediaOutput(TypedDict):
    type: str
    filename: str
    subtitles: NotRequired[bool]
    metadata: NotRequired[dict[str, str]]


class MediaProcessing(TypedDict):
    status: Literal["processing"]
    operation: str
    source: str
    urls: list[str]
    output: MediaOutput
    audio: NotRequired[dict[str, object]]
    is_hls: NotRequired[bool]


class MediaError(TypedDict):
    status: Literal["error"]
    code: str
    context: NotRequired[dict[str, str | int | float]]


type MediaResult = MediaFile | MediaSelection | MediaProcessing | MediaError

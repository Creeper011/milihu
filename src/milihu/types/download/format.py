"""Output formats available for downloads."""

from enum import StrEnum


class DownloadFormat(StrEnum):
    MP3 = "mp3"
    OGG = "ogg"
    WAV = "wav"
    OPUS = "opus"
    MP4 = "mp4"
    WEBM = "webm"
    MKV = "mkv"


AUDIO_FORMATS: frozenset[DownloadFormat] = frozenset(
    {
        DownloadFormat.MP3,
        DownloadFormat.OGG,
        DownloadFormat.WAV,
        DownloadFormat.OPUS,
    }
)

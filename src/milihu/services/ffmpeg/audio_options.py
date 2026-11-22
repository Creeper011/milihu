"""FFmpeg audio filter and CLI option builders for voice playback."""

from milihu.types.default.voice import (
    DEFAULT_AUDIO_SAMPLE_RATE_HZ,
    DEFAULT_FFMPEG_OPTIONS,
    DEFAULT_PLAYBACK_OPTIONS,
)
from milihu.types.voice import PlaybackOptions, SeekOffset


def build_audio_filter(
    playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
    *,
    seek_seconds: SeekOffset = 0.0,
) -> str | None:
    """Build an FFmpeg ``-af`` filtergraph for ``playback`` and ``seek_seconds``.

    Seeking uses ``atrim`` inside ``-af`` rather than input ``-ss`` so HTTP Ogg/Opus
    tunnels are always read sequentially from byte 0 without mid-stream range seeks.
    """
    stages: list[str] = []
    if seek_seconds > 0.0:
        stages.append(f"atrim=start={seek_seconds:.3f}")
        stages.append("asetpts=PTS-STARTPTS")
    if not playback.is_normal_speed:
        if playback.preserve_pitch:
            remaining = playback.speed
            while remaining < 0.5:
                stages.append("atempo=0.5")
                remaining /= 0.5
            while remaining > 2.0:
                stages.append("atempo=2.0")
                remaining /= 2.0
            stages.append(f"atempo={remaining:.6g}")
        else:
            target_rate = round(DEFAULT_AUDIO_SAMPLE_RATE_HZ * playback.speed)
            stages.append(
                f"aresample={DEFAULT_AUDIO_SAMPLE_RATE_HZ},"
                f"asetrate={target_rate},"
                f"aresample={DEFAULT_AUDIO_SAMPLE_RATE_HZ}"
            )
    return ",".join(stages) if stages else None


def build_ffmpeg_options(
    playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
    *,
    seek_seconds: SeekOffset = 0.0,
) -> str:
    """Build FFmpeg output ``options`` including audio filters when active."""
    audio_filter = build_audio_filter(playback, seek_seconds=seek_seconds)
    if audio_filter is None:
        return DEFAULT_FFMPEG_OPTIONS
    return f"{DEFAULT_FFMPEG_OPTIONS} -af {audio_filter}"

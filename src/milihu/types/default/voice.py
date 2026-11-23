"""Default settings for voice connections and music playback."""

from pydantic import PositiveFloat, PositiveInt

from milihu.types.common import NonEmptyStr
from milihu.types.voice import PlaybackOptions, PlaybackSpeed

DEFAULT_VOICE_IDLE_TIMEOUT_SECONDS: PositiveFloat = 180.0
DEFAULT_MAX_QUEUE_DISPLAY: PositiveInt = 10
DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS: PositiveFloat = 10.0
DEFAULT_ICY_POLL_SECONDS: PositiveFloat = 15.0
DEFAULT_PLAYBACK_SPEED: PlaybackSpeed = 1.0
DEFAULT_PRESERVE_PITCH: bool = True
DEFAULT_PLAYBACK_OPTIONS: PlaybackOptions = PlaybackOptions(
    speed=DEFAULT_PLAYBACK_SPEED,
    preserve_pitch=DEFAULT_PRESERVE_PITCH,
)
DEFAULT_AUDIO_SAMPLE_RATE_HZ: PositiveInt = 48_000
DEFAULT_FFMPEG_BEFORE_OPTIONS: NonEmptyStr = (
    "-protocol_whitelist http,https,tcp,tls,crypto "
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
)
DEFAULT_FFMPEG_OPTIONS: NonEmptyStr = "-vn"
DISCORD_VOICE_STATUS_MAX_LENGTH: PositiveInt = 500

LOCAL_STREAM_FFMPEG_BEFORE_OPTIONS = (
    "-protocol_whitelist http,https,tcp,tls,crypto -seekable 0 "
    "-f s16le -ar 48000 -ac 2"
)

FAST_FAILURE_THRESHOLD_SECONDS = 2.0
MAX_CONSECUTIVE_FAST_FAILURES = 3

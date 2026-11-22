"""Default settings for voice connections and music playback."""

from pydantic import PositiveFloat, PositiveInt

DEFAULT_VOICE_IDLE_TIMEOUT_SECONDS: PositiveFloat = 180.0
DEFAULT_MAX_QUEUE_DISPLAY: PositiveInt = 10
DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS: PositiveFloat = 10.0
DEFAULT_ICY_POLL_SECONDS: PositiveFloat = 15.0

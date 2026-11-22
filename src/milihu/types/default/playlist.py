"""Default settings and constants for playlist resolution."""

from pydantic import PositiveFloat, PositiveInt

DEFAULT_MAX_PLAYLIST_TRACKS: PositiveInt = 100
DEFAULT_PLAYLIST_TIMEOUT_SECONDS: PositiveFloat = 10.0
PLAYLIST_FALLBACK_WARNING = (
    "Could not load the playlist; playing the single track instead."
)

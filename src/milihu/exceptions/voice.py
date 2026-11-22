"""Voice and music playback exceptions."""


class VoiceError(Exception):
    """Raised when a voice or music playback operation cannot proceed."""


class BackendUnavailableError(VoiceError):
    """Raised when audio resolution fails due to a backend outage (5xx/connection)."""

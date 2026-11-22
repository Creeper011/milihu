"""Voice connection and stream probing contracts for application services."""

from typing import Protocol

from pydantic import HttpUrl

from milihu.types.default.voice import DEFAULT_PLAYBACK_OPTIONS
from milihu.types.voice import (
    ChannelEmptyCallback,
    ChannelMoveCallback,
    DisconnectCallback,
    PlaybackOptions,
    ProbedAudio,
    SeekOffset,
    StreamMetadata,
    TrackEndCallback,
)


class VoiceManager(Protocol):
    """Low-level voice channel connection and audio playback contract."""

    def connected_channel_id(self, guild_id: int) -> int | None: ...

    def non_bot_member_count(self, guild_id: int) -> int: ...

    def is_playing(self, guild_id: int) -> bool: ...

    def is_paused(self, guild_id: int) -> bool: ...

    async def connect(self, guild_id: int, channel_id: int) -> None: ...

    async def disconnect(self, guild_id: int) -> None: ...

    def play(
        self,
        guild_id: int,
        stream_url: str,
        *,
        after: TrackEndCallback,
        playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
        seek_seconds: SeekOffset = 0.0,
    ) -> None: ...

    def stop(self, guild_id: int) -> None: ...

    def pause(self, guild_id: int) -> bool: ...

    def resume(self, guild_id: int) -> bool: ...

    async def set_channel_status(
        self,
        guild_id: int,
        status: str | None,
        *,
        channel_id: int | None = None,
    ) -> None: ...

    def set_on_disconnect(self, callback: DisconnectCallback | None) -> None: ...

    def set_on_channel_move(self, callback: ChannelMoveCallback | None) -> None: ...

    def set_on_channel_empty_changed(
        self, callback: ChannelEmptyCallback | None
    ) -> None: ...

    async def aclose(self) -> None: ...


class StreamProber(Protocol):
    """HTTP direct-audio probing, SSRF validation, and ICY metadata reader."""

    async def validate_url(self, url: str) -> None: ...

    async def probe_audio(self, url: HttpUrl) -> ProbedAudio | None: ...

    async def fetch_icy_title(self, stream_url: str) -> StreamMetadata: ...

    async def aclose(self) -> None: ...

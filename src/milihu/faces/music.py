"""Service protocol for music playback and queue management."""

from typing import Protocol

from milihu.types.voice import (
    EnqueueResult,
    LoopMode,
    PlaybackOptions,
    PlayRequest,
    QueueSnapshot,
    Track,
    TrackStartCallback,
)


class MusicPlayer(Protocol):
    """Protocol consumed by Discord cogs to control music playback."""

    def set_on_track_start(self, callback: TrackStartCallback | None) -> None: ...

    async def enqueue(
        self,
        guild_id: int,
        channel_id: int | None,
        url: PlayRequest | str,
        *,
        requested_by: int,
    ) -> EnqueueResult: ...

    async def skip(self, guild_id: int, channel_id: int | None) -> Track: ...

    async def pause(self, guild_id: int, channel_id: int | None) -> Track: ...

    async def resume(self, guild_id: int, channel_id: int | None) -> Track: ...

    async def stop(self, guild_id: int, channel_id: int | None) -> None: ...

    async def set_loop(
        self,
        guild_id: int,
        channel_id: int | None,
        mode: LoopMode,
    ) -> LoopMode: ...

    async def set_speed(
        self,
        guild_id: int,
        channel_id: int | None,
        *,
        speed: float | None = None,
        preserve_pitch: bool | None = None,
    ) -> PlaybackOptions: ...

    async def remove(
        self,
        guild_id: int,
        channel_id: int | None,
        position: int,
    ) -> Track: ...

    async def clear(self, guild_id: int, channel_id: int | None) -> int: ...

    async def shuffle(self, guild_id: int, channel_id: int | None) -> int: ...

    def get_queue(self, guild_id: int) -> QueueSnapshot: ...

    async def aclose(self) -> None: ...

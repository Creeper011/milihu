"""Voice and music playback type definitions."""

from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.common import NonEmptyStr


class LoopMode(StrEnum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class PlayRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: HttpUrl


class Track(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: HttpUrl
    title: NonEmptyStr
    requested_by: int
    is_live: bool = False


class EnqueueResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    track: Track
    position: int
    started_immediately: bool
    enqueued_count: int = 1
    playlist_title: NonEmptyStr | None = None
    warning: NonEmptyStr | None = None


class QueueSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    guild_id: int
    channel_id: int | None
    current: Track | None
    upcoming: tuple[Track, ...]
    loop_mode: LoopMode
    is_paused: bool


class ProbedAudio(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_url: NonEmptyStr
    title: NonEmptyStr
    is_live: bool = False


class StreamMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: bool
    title: NonEmptyStr | None = None


type TrackStartCallback = Callable[[int, int, Track], Awaitable[None]]
type TrackEndCallback = Callable[[Exception | None], Awaitable[None]]
type DisconnectCallback = Callable[[int], Awaitable[None]]
type ChannelMoveCallback = Callable[[int, int, int], Awaitable[None]]
type ChannelEmptyCallback = Callable[[int, bool], Awaitable[None]]

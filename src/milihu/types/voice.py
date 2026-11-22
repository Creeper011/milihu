"""Voice and music playback type definitions."""

import math
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from milihu.types.common import NonEmptyStr

MIN_PLAYBACK_SPEED: Final[float] = 0.25
MAX_PLAYBACK_SPEED: Final[float] = 4.0

PlaybackSpeed = Annotated[
    float,
    Field(ge=MIN_PLAYBACK_SPEED, le=MAX_PLAYBACK_SPEED, allow_inf_nan=False),
]
SeekOffset = Annotated[
    float,
    Field(ge=0.0, allow_inf_nan=False),
]


class PlaybackOptions(BaseModel):
    """Validated playback speed and pitch preservation settings."""

    model_config = ConfigDict(frozen=True)

    speed: PlaybackSpeed = 1.0
    preserve_pitch: bool = True

    @property
    def is_normal_speed(self) -> bool:
        return math.isclose(self.speed, 1.0, rel_tol=0.0, abs_tol=1e-6)


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
    playback: PlaybackOptions = Field(default_factory=PlaybackOptions)


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

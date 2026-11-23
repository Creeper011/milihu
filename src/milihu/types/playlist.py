"""Playlist and audio resolution type definitions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.common import NonEmptyStr

type StreamHeaders = tuple[tuple[NonEmptyStr, NonEmptyStr], ...]


class AudioTrackItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: HttpUrl
    title: NonEmptyStr
    encoded: NonEmptyStr
    is_live: bool = False


class ResolvedPlaylist(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: NonEmptyStr | None = None
    entries: tuple[AudioTrackItem, ...]


class AudioLoadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["track", "playlist", "search", "empty"]
    track: AudioTrackItem | None = None
    playlist: ResolvedPlaylist | None = None
    tracks: tuple[AudioTrackItem, ...] = ()


class ResolvedStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_url: NonEmptyStr
    title: NonEmptyStr
    is_live: bool = False
    is_direct: bool = False
    headers: StreamHeaders = ()

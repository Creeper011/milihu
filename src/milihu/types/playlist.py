"""Playlist and audio resolution type definitions."""

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.common import NonEmptyStr


class PlaylistEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: HttpUrl
    title: NonEmptyStr


class ResolvedPlaylist(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: NonEmptyStr | None = None
    entries: tuple[PlaylistEntry, ...]


class ResolvedStream(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream_url: NonEmptyStr
    title: NonEmptyStr
    is_live: bool = False
    is_direct: bool = False

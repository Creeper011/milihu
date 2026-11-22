"""Playlist expansion contract for application services."""

from typing import Protocol

from pydantic import HttpUrl

from milihu.types.playlist import ResolvedPlaylist


class PlaylistResolver(Protocol):
    """Expand playlist URLs into ordered track entries without resolving audio."""

    async def resolve(self, url: HttpUrl) -> ResolvedPlaylist | None: ...

"""Universal audio track, playlist, search, and stream resolution contract."""

from typing import Protocol

from pydantic import HttpUrl

from milihu.types.playlist import AudioLoadResult, AudioTrackItem, ResolvedStream


class AudioBackend(Protocol):
    """Resolve URLs, playlists, searches, and playable audio streams."""

    async def load(self, url: HttpUrl) -> AudioLoadResult: ...

    async def search(self, query: str) -> tuple[AudioTrackItem, ...]: ...

    async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream: ...

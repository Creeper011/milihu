"""Audio, playlist, and search resolution orchestrator for music playback."""

import logging
from collections.abc import Sequence

from pydantic import HttpUrl

from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.types.playlist import (
    AudioTrackItem,
    ResolvedPlaylist,
    ResolvedStream,
    StreamHeaders,
)
from milihu.types.voice import Track
from milihu.usecases.faces.audio import AudioBackend
from milihu.usecases.faces.voice import StreamProber


class TrackResolver:
    """Resolve media streams, playlists, searches, and direct-stream fallbacks."""

    def __init__(
        self,
        audio_backend: AudioBackend | None,
        stream_probe: StreamProber,
        *,
        max_backend_failures: int = 3,
    ) -> None:
        self.audio_backend = audio_backend
        self.stream_probe = stream_probe
        self.max_backend_failures = max_backend_failures
        self._logger = logging.getLogger(__name__)

    async def resolve_direct_audio(
        self,
        url: HttpUrl,
        *,
        fallback_error: VoiceError | None = None,
    ) -> ResolvedStream:
        """Probe ``url`` as a direct HTTP audio stream."""
        direct = await self.stream_probe.probe_audio(url)
        if direct is not None:
            return ResolvedStream(
                stream_url=direct.stream_url,
                title=direct.title,
                is_live=direct.is_live,
                is_direct=True,
            )
        if fallback_error is not None:
            raise fallback_error
        raise VoiceError("This URL does not provide playable audio.")

    async def resolve_item_stream(self, item: AudioTrackItem) -> ResolvedStream:
        """Resolve an ``AudioTrackItem`` into a playable ``ResolvedStream``."""
        if self.audio_backend is None:
            raise VoiceError("Audio backend is not configured.")
        return await self.audio_backend.resolve_stream(item)

    async def resolve_input(
        self, url: HttpUrl
    ) -> (
        tuple[ResolvedPlaylist, None, None]
        | tuple[None, AudioTrackItem | None, ResolvedStream]
    ):
        """Resolve ``url`` as a playlist, backend track, or direct audio stream."""
        backend_error: VoiceError | None = None
        if self.audio_backend is not None:
            try:
                loaded = await self.audio_backend.load(url)
            except VoiceError as exc:
                backend_error = exc
            else:
                if loaded.kind == "playlist" and loaded.playlist is not None:
                    return loaded.playlist, None, None
                if loaded.kind == "track" and loaded.track is not None:
                    try:
                        stream = await self.audio_backend.resolve_stream(loaded.track)
                    except VoiceError as exc:
                        backend_error = exc
                    else:
                        return None, loaded.track, stream
                elif loaded.kind == "search" and loaded.tracks:
                    try:
                        item, stream = await self.resolve_first_item(loaded.tracks)
                    except VoiceError as exc:
                        backend_error = exc
                    else:
                        return None, item, stream

        direct_stream = await self.resolve_direct_audio(
            url, fallback_error=backend_error
        )
        return None, None, direct_stream

    async def resolve_first_playable(
        self, items: Sequence[AudioTrackItem]
    ) -> tuple[list[AudioTrackItem], ResolvedStream]:
        """Skip unavailable leading playlist tracks; abort after consecutive outages."""
        backend_failures = 0
        last_error: VoiceError | None = None
        for idx, item in enumerate(items):
            try:
                stream = await self.resolve_item_stream(item)
            except BackendUnavailableError as exc:
                backend_failures += 1
                last_error = exc
                if backend_failures >= self.max_backend_failures:
                    raise
                continue
            except VoiceError as exc:
                backend_failures = 0
                last_error = exc
                continue

            first_item = (
                item.model_copy(update={"is_live": stream.is_live})
                if stream.is_live != item.is_live
                else item
            )
            return [first_item, *items[idx + 1 :]], stream

        raise last_error or VoiceError("Could not load tracks from this playlist.")

    async def resolve_first_item(
        self, items: Sequence[AudioTrackItem]
    ) -> tuple[AudioTrackItem, ResolvedStream]:
        """Resolve the first playable candidate; abort after consecutive outages."""
        if not items:
            raise VoiceError("No videos found for this search.")
        backend_failures = 0
        last_error: VoiceError | None = None
        for item in items:
            try:
                stream = await self.resolve_item_stream(item)
            except BackendUnavailableError as exc:
                backend_failures += 1
                last_error = exc
                self._logger.warning(
                    "Skipping search candidate %s after backend error: %s",
                    item.source_url,
                    exc,
                )
                if backend_failures >= self.max_backend_failures:
                    raise
                continue
            except VoiceError as exc:
                backend_failures = 0
                last_error = exc
                self._logger.warning(
                    "Skipping unplayable search candidate %s: %s",
                    item.source_url,
                    exc,
                )
                continue
            return item, stream

        raise last_error or VoiceError("No videos found for this search.")

    async def resolve_search(
        self, query: str
    ) -> tuple[AudioTrackItem, ResolvedStream]:
        """Search ``query`` and resolve the first playable candidate."""
        if self.audio_backend is None:
            raise VoiceError("Search is unavailable.")
        candidates = await self.audio_backend.search(query)
        return await self.resolve_first_item(candidates)

    async def prepare_stream(
        self,
        track: Track,
        *,
        encoded: str | None,
        stream_url: str | None,
        headers: StreamHeaders = (),
        is_direct: bool,
    ) -> ResolvedStream:
        """Resolve a deferred track stream or re-validate a direct stream."""
        if stream_url is not None:
            if is_direct:
                await self.stream_probe.validate_url(stream_url)
            return ResolvedStream(
                stream_url=stream_url,
                title=track.title,
                is_live=track.is_live,
                is_direct=is_direct,
                headers=headers,
            )
        if encoded is not None:
            item = AudioTrackItem(
                source_url=track.source_url,
                title=track.title,
                encoded=encoded,
                is_live=track.is_live,
            )
            return await self.resolve_item_stream(item)
        if is_direct:
            return await self.resolve_direct_audio(track.source_url)
        _, _, resolved = await self.resolve_input(track.source_url)
        if resolved is None:
            raise VoiceError("This URL does not provide playable audio.")
        return resolved

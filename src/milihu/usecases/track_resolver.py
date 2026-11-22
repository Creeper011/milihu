"""Audio and playlist resolution orchestrator for music playback."""

import logging
from collections.abc import Sequence
from pathlib import PurePath

from pydantic import HttpUrl

from milihu.exceptions.media import MediaBackendError
from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.types.default.playlist import PLAYLIST_FALLBACK_WARNING
from milihu.types.download.format import DownloadFormat
from milihu.types.playlist import ResolvedPlaylist, ResolvedStream
from milihu.types.voice import Track
from milihu.usecases.faces.media import MediaBackend
from milihu.usecases.faces.playlist import PlaylistResolver
from milihu.usecases.faces.voice import StreamProber


class TrackResolver:
    """Resolve media streams, playlists, fallback policy, and outage classification."""

    def __init__(
        self,
        media_backend: MediaBackend | None,
        stream_probe: StreamProber,
        playlist_resolver: PlaylistResolver | None = None,
        *,
        max_backend_failures: int = 3,
    ) -> None:
        self.media_backend = media_backend
        self.stream_probe = stream_probe
        self.playlist_resolver = playlist_resolver
        self.max_backend_failures = max_backend_failures
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def is_backend_outage(exc: BaseException) -> bool:
        if isinstance(exc, MediaBackendError):
            return exc.status is None or exc.status >= 500
        return isinstance(exc, (TimeoutError, RuntimeError))

    async def resolve_audio(self, url: HttpUrl) -> ResolvedStream:
        """Resolve ``url`` to a playable audio stream via backend or direct probe."""
        backend_error: str | None = None
        backend_outage = False
        if self.media_backend is not None:
            try:
                result = await self.media_backend.resolve(
                    str(url), format=DownloadFormat.OPUS
                )
            except (RuntimeError, TimeoutError) as exc:
                backend_error = f"Could not resolve audio: {exc}"
                backend_outage = self.is_backend_outage(exc)
            else:
                if result["status"] == "file":
                    title = PurePath(result["filename"]).stem.strip() or str(url)
                    return ResolvedStream(
                        stream_url=result["url"],
                        title=title,
                        is_live=False,
                        is_direct=False,
                    )
                if result["status"] == "selection" and "audio" in result:
                    raw_name = result.get("audio_filename", "")
                    title = PurePath(raw_name).stem.strip() if raw_name else str(url)
                    return ResolvedStream(
                        stream_url=result["audio"],
                        title=title or str(url),
                        is_live=False,
                        is_direct=False,
                    )
                if result["status"] == "error":
                    backend_error = f"Could not resolve audio: {result['code']}"
                else:
                    backend_error = "This URL does not provide playable audio."

        direct = await self.stream_probe.probe_audio(url)
        if direct is not None:
            return ResolvedStream(
                stream_url=direct.stream_url,
                title=direct.title,
                is_live=direct.is_live,
                is_direct=True,
            )

        message = backend_error or "This URL does not provide playable audio."
        if backend_outage:
            raise BackendUnavailableError(message)
        raise VoiceError(message)

    async def resolve_input(
        self, url: HttpUrl
    ) -> tuple[ResolvedPlaylist, None, None] | tuple[None, ResolvedStream, str | None]:
        """Resolve ``url`` as a playlist or single stream (with fallback policy)."""
        playlist_error: VoiceError | None = None
        if self.playlist_resolver is not None:
            try:
                playlist = await self.playlist_resolver.resolve(url)
            except VoiceError as exc:
                playlist_error = exc
            else:
                if playlist is not None:
                    return playlist, None, None

        try:
            stream = await self.resolve_audio(url)
        except VoiceError:
            if playlist_error is not None:
                raise playlist_error from None
            raise

        warning = PLAYLIST_FALLBACK_WARNING if playlist_error is not None else None
        return None, stream, warning

    async def resolve_first_playable(
        self, tracks: list[Track]
    ) -> tuple[list[Track], ResolvedStream]:
        """Skip unavailable leading tracks; abort after consecutive backend outages."""
        backend_failures = 0
        last_error: VoiceError | None = None
        for idx, track in enumerate(tracks):
            try:
                stream = await self.resolve_audio(track.source_url)
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

            first_track = (
                track.model_copy(update={"is_live": stream.is_live})
                if stream.is_live != track.is_live
                else track
            )
            return [first_track, *tracks[idx + 1 :]], stream

        raise last_error or VoiceError("Could not load tracks from this playlist.")

    async def resolve_first_audio(
        self, urls: Sequence[HttpUrl]
    ) -> tuple[HttpUrl, ResolvedStream]:
        """Resolve the first playable candidate URL; abort after consecutive outages."""
        if not urls:
            raise VoiceError("No videos found for this search.")
        backend_failures = 0
        last_error: VoiceError | None = None
        for url in urls:
            try:
                stream = await self.resolve_audio(url)
            except BackendUnavailableError as exc:
                backend_failures += 1
                last_error = exc
                self._logger.warning(
                    "Skipping search candidate %s after backend error: %s", url, exc
                )
                if backend_failures >= self.max_backend_failures:
                    raise
                continue
            except VoiceError as exc:
                backend_failures = 0
                last_error = exc
                self._logger.warning(
                    "Skipping unplayable search candidate %s: %s", url, exc
                )
                continue
            return url, stream

        raise last_error or VoiceError("No videos found for this search.")

    async def prepare_stream(
        self,
        source_url: HttpUrl,
        stream_url: str | None,
        *,
        is_direct: bool,
    ) -> tuple[str, bool]:
        """Resolve a deferred stream URL or re-validate a cached direct stream."""
        if stream_url is None:
            resolved = await self.resolve_audio(source_url)
            return resolved.stream_url, resolved.is_direct
        if is_direct:
            await self.stream_probe.validate_url(stream_url)
        return stream_url, is_direct

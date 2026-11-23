"""NodeLink HTTP adapter for universal track, playlist, search, and streams."""

from collections.abc import Mapping
from typing import Self, TypeIs
from urllib.parse import quote

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout
from pydantic import HttpUrl, TypeAdapter, ValidationError

from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.types.default.nodelink import (
    GENERIC_AUTHORS,
    OUTAGE_MARKERS,
)
from milihu.types.default.playlist import (
    DEFAULT_MAX_PLAYLIST_TRACKS,
    DEFAULT_MAX_SEARCH_RESULTS,
)
from milihu.types.playlist import (
    AudioLoadResult,
    AudioTrackItem,
    ResolvedPlaylist,
    ResolvedStream,
)


class NodeLinkClient:
    """Share one HTTP session and translate NodeLink v4 responses to domain types."""

    _url_adapter: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

    def __init__(
        self,
        base_url: str,
        password: str,
        *,
        max_playlist_tracks: int = DEFAULT_MAX_PLAYLIST_TRACKS,
        max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS,
        timeout_seconds: float = 30.0,
    ) -> None:
        if max_playlist_tracks < 1:
            raise ValueError("max_playlist_tracks must be at least 1")
        if max_search_results < 1:
            raise ValueError("max_search_results must be at least 1")
        self.base_url = base_url.rstrip("/") + "/"
        self._password = password
        self._max_playlist_tracks = max_playlist_tracks
        self._max_search_results = max_search_results
        self._timeout = ClientTimeout(total=timeout_seconds)
        self.session: ClientSession | None = None

    @staticmethod
    def _is_object(value: object) -> TypeIs[Mapping[str, object]]:
        return isinstance(value, Mapping)

    @staticmethod
    def _is_list(value: object) -> TypeIs[list[object]]:
        return isinstance(value, list)

    async def __aenter__(self) -> Self:
        self.session = ClientSession(
            base_url=self.base_url,
            headers={
                "Authorization": self._password,
                "Accept": "application/json",
            },
            timeout=self._timeout,
        )
        try:
            async with self.session.get("/version") as response:
                response.raise_for_status()
                version = (await response.text()).strip()
                if not version or version == "unknown":
                    raise RuntimeError(
                        "The configured NodeLink instance is unavailable"
                    )
        except BaseException:
            await self.session.close()
            self.session = None
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    @staticmethod
    def format_title(title: str, author: str | None) -> str:
        clean_title = title.strip()
        clean_author = (author or "").strip()
        if (
            clean_author
            and clean_author.lower() not in GENERIC_AUTHORS
            and clean_author.lower() not in clean_title.lower()
        ):
            return f"{clean_author} - {clean_title}"
        return clean_title

    @classmethod
    def _parse_track(
        cls,
        raw: object,
        *,
        fallback_url: HttpUrl | None = None,
    ) -> AudioTrackItem | None:
        if not cls._is_object(raw):
            return None
        encoded = raw.get("encoded")
        info = raw.get("info")
        if (
            not isinstance(encoded, str)
            or not encoded.strip()
            or not cls._is_object(info)
        ):
            return None

        source_url: HttpUrl | None = None
        raw_uri = info.get("uri")
        if isinstance(raw_uri, str) and raw_uri.strip():
            try:
                source_url = cls._url_adapter.validate_python(raw_uri.strip())
            except ValidationError:
                source_url = None
        if source_url is None:
            source_url = fallback_url
        if source_url is None:
            return None

        raw_title = info.get("title")
        base_title = (
            raw_title.strip()
            if isinstance(raw_title, str) and raw_title.strip()
            else str(source_url)
        )
        raw_author = info.get("author")
        author = raw_author.strip() if isinstance(raw_author, str) else None
        title = cls.format_title(base_title, author) or str(source_url)
        is_live = info.get("isStream") is True
        return AudioTrackItem(
            source_url=source_url,
            title=title,
            encoded=encoded.strip(),
            is_live=is_live,
        )

    @classmethod
    def _parse_track_list(
        cls,
        raw_items: object,
        *,
        fallback_url: HttpUrl | None = None,
    ) -> list[AudioTrackItem]:
        if not cls._is_list(raw_items):
            return []
        items: list[AudioTrackItem] = []
        for raw in raw_items:
            parsed = cls._parse_track(raw, fallback_url=fallback_url)
            if parsed is not None:
                items.append(parsed)
        return items

    async def _request_loadtracks(self, identifier: str) -> Mapping[str, object]:
        if self.session is None:
            raise RuntimeError("Audio backend is not connected")
        try:
            async with self.session.get(
                "/v4/loadtracks", params={"identifier": identifier}
            ) as response:
                if response.status >= 500:
                    raise BackendUnavailableError(
                        "Audio backend is temporarily unavailable."
                    )
                if response.status >= 400:
                    raise VoiceError("Could not resolve audio for this input.")
                data: object = await response.json()
        except (ClientError, TimeoutError, ValueError) as exc:
            raise BackendUnavailableError(
                "Audio backend is temporarily unavailable."
            ) from exc

        if not self._is_object(data):
            raise BackendUnavailableError("Unsupported audio backend response.")
        return data

    @classmethod
    def _raise_load_error(cls, data: object) -> None:
        if cls._is_object(data):
            raw_msg = data.get("message")
            message = (
                raw_msg.strip()
                if isinstance(raw_msg, str) and raw_msg.strip()
                else "Could not resolve audio."
            )
            if data.get("severity") == "fault":
                raise BackendUnavailableError(f"Could not resolve audio: {message}")
            raise VoiceError(f"Could not resolve audio: {message}")
        raise VoiceError("Could not resolve audio.")

    async def load(self, url: HttpUrl) -> AudioLoadResult:
        payload = await self._request_loadtracks(str(url))
        load_type = payload.get("loadType")
        data = payload.get("data")

        match load_type:
            case "track":
                track = self._parse_track(data, fallback_url=url)
                if track is None:
                    return AudioLoadResult(kind="empty")
                return AudioLoadResult(kind="track", track=track)
            case "playlist":
                if not self._is_object(data):
                    return AudioLoadResult(kind="empty")
                all_tracks = self._parse_track_list(
                    data.get("tracks"), fallback_url=url
                )
                if not all_tracks:
                    return AudioLoadResult(kind="empty")
                info = data.get("info")
                title: str | None = None
                start_index = 0
                if self._is_object(info):
                    raw_name = info.get("name")
                    if isinstance(raw_name, str) and raw_name.strip():
                        title = raw_name.strip()
                    selected = info.get("selectedTrack")
                    if (
                        isinstance(selected, int)
                        and not isinstance(selected, bool)
                        and 0 <= selected < len(all_tracks)
                    ):
                        start_index = selected
                entries = tuple(
                    all_tracks[start_index : start_index + self._max_playlist_tracks]
                )
                if not entries:
                    return AudioLoadResult(kind="empty")
                return AudioLoadResult(
                    kind="playlist",
                    playlist=ResolvedPlaylist(title=title, entries=entries),
                )
            case "search":
                tracks = tuple(
                    self._parse_track_list(data, fallback_url=url)[
                        : self._max_search_results
                    ]
                )
                if not tracks:
                    return AudioLoadResult(kind="empty")
                return AudioLoadResult(kind="search", tracks=tracks)
            case "empty":
                return AudioLoadResult(kind="empty")
            case "error":
                self._raise_load_error(data)
                return AudioLoadResult(kind="empty")
            case _:
                raise BackendUnavailableError("Unsupported audio backend response.")

    async def search(self, query: str) -> tuple[AudioTrackItem, ...]:
        cleaned = query.strip()
        if not cleaned:
            raise VoiceError("Enter a URL or search query.")

        for idx, prefix in enumerate(("ytmsearch", "ytsearch")):
            try:
                payload = await self._request_loadtracks(f"{prefix}:{cleaned}")
            except VoiceError:
                if idx == 0:
                    continue
                raise
            load_type = payload.get("loadType")
            data = payload.get("data")
            if load_type == "search":
                tracks = tuple(
                    self._parse_track_list(data)[: self._max_search_results]
                )
                if tracks:
                    return tracks
            elif load_type == "track":
                track = self._parse_track(data)
                if track is not None:
                    return (track,)
            elif load_type == "error" and idx > 0:
                self._raise_load_error(data)

        raise VoiceError("No videos found for this search.")

    @classmethod
    def _extract_stream_error(cls, data: object) -> str | None:
        if not cls._is_object(data):
            return None
        exc = data.get("exception")
        if cls._is_object(exc):
            msg = exc.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        msg = data.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
        return None

    async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream:
        if self.session is None:
            raise RuntimeError("Audio backend is not connected")
        try:
            async with self.session.get(
                "/v4/trackstream", params={"encodedTrack": item.encoded}
            ) as response:
                status = response.status
                try:
                    data: object = await response.json()
                except (ClientError, ValueError):
                    data = None
                if status in (502, 503, 504):
                    raise BackendUnavailableError(
                        "Audio backend is temporarily unavailable."
                    )
                if status >= 400:
                    detail = self._extract_stream_error(data)
                    if detail is not None:
                        lowered = detail.lower()
                        if any(marker in lowered for marker in OUTAGE_MARKERS):
                            raise BackendUnavailableError(
                                f"Could not resolve audio: {detail}"
                            )
                        raise VoiceError(f"Could not resolve audio: {detail}")
                    if status >= 500:
                        raise BackendUnavailableError(
                            "Audio backend is temporarily unavailable."
                        )
                    raise VoiceError("This track does not provide playable audio.")
        except ClientResponseError as exc:
            if exc.status >= 500:
                raise BackendUnavailableError(
                    "Audio backend is temporarily unavailable."
                ) from exc
            raise VoiceError("Could not resolve audio for this track.") from exc
        except (ClientError, TimeoutError) as exc:
            raise BackendUnavailableError(
                "Audio backend is temporarily unavailable."
            ) from exc

        detail = self._extract_stream_error(data)
        if detail is not None and (
            not self._is_object(data) or "exception" in data or "url" not in data
        ):
            lowered = detail.lower()
            if any(marker in lowered for marker in OUTAGE_MARKERS):
                raise BackendUnavailableError(f"Could not resolve audio: {detail}")
            raise VoiceError(f"Could not resolve audio: {detail}")

        encoded_param = quote(item.encoded, safe="")
        stream_url = (
            f"{self.base_url}v4/loadstream?encodedTrack={encoded_param}&format=raw"
        )
        return ResolvedStream(
            stream_url=stream_url,
            title=item.title,
            is_live=item.is_live,
            is_direct=False,
            headers=(("Authorization", self._password),),
        )

"""YouTube playlist and Mix/Radio resolver via InnerTube /next."""

import re
from collections.abc import Mapping
from typing import TypeIs
from urllib.parse import parse_qs, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import HttpUrl, TypeAdapter, ValidationError

from milihu.exceptions.voice import VoiceError
from milihu.types.default.playlist import (
    DEFAULT_MAX_PLAYLIST_TRACKS,
    DEFAULT_PLAYLIST_TIMEOUT_SECONDS,
)
from milihu.types.playlist import PlaylistEntry, ResolvedPlaylist

YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)
YOUTUBE_NEXT_URL = "https://www.youtube.com/youtubei/v1/next"
YOUTUBE_CLIENT_CONTEXT: dict[str, dict[str, str]] = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20250101.00.00",
        "hl": "en",
        "gl": "US",
    }
}
YOUTUBE_VIDEO_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class YouTubePlaylistResolver:
    """Resolve YouTube playlists and Mixes via InnerTube without resolving audio."""

    _url_adapter: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

    def __init__(
        self,
        *,
        max_tracks: int = DEFAULT_MAX_PLAYLIST_TRACKS,
        timeout_seconds: float = DEFAULT_PLAYLIST_TIMEOUT_SECONDS,
        next_url: str = YOUTUBE_NEXT_URL,
    ) -> None:
        self._max_tracks = max_tracks
        self._timeout = ClientTimeout(total=timeout_seconds)
        self._next_url = next_url

    @staticmethod
    def _is_json_object(value: object) -> TypeIs[Mapping[str, object]]:
        return isinstance(value, Mapping)

    @staticmethod
    def _is_json_array(value: object) -> TypeIs[list[object]]:
        return isinstance(value, list)

    @classmethod
    def _extract_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if not cls._is_json_object(value):
            return None
        simple = value.get("simpleText")
        if isinstance(simple, str) and simple.strip():
            return simple.strip()
        runs = value.get("runs")
        if cls._is_json_array(runs):
            parts: list[str] = []
            for run in runs:
                if cls._is_json_object(run):
                    text = run.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            joined = "".join(parts).strip()
            if joined:
                return joined
        return None

    @staticmethod
    def parse_url(url: HttpUrl) -> tuple[str, str | None] | None:
        """Return ``(playlist_id, video_id)`` for YouTube URLs with ``list=``."""
        parsed = urlsplit(str(url))
        host = (parsed.hostname or "").lower()
        if host not in YOUTUBE_HOSTS:
            return None
        params = parse_qs(parsed.query)
        playlist_id = (params.get("list") or [""])[0].strip()
        if not playlist_id:
            return None

        raw_video_id = ""
        if host == "youtu.be":
            segments = [seg for seg in parsed.path.split("/") if seg]
            if segments:
                raw_video_id = segments[0].strip()
        else:
            raw_video_id = (params.get("v") or [""])[0].strip()

        video_id = (
            raw_video_id if YOUTUBE_VIDEO_ID_PATTERN.fullmatch(raw_video_id) else None
        )
        return playlist_id, video_id

    @classmethod
    def extract_from_next(
        cls,
        data: object,
        *,
        video_id: str | None = None,
        max_tracks: int = DEFAULT_MAX_PLAYLIST_TRACKS,
    ) -> ResolvedPlaylist | None:
        """Parse playable entries from a YouTube InnerTube ``/next`` payload."""
        current: object = data
        for key in ("contents", "twoColumnWatchNextResults", "playlist", "playlist"):
            if not cls._is_json_object(current):
                return None
            current = current.get(key)
        if not cls._is_json_object(current):
            return None
        playlist = current
        contents = playlist.get("contents")
        if not cls._is_json_array(contents):
            return None

        # shortcut: single /next page (~200 tracks max); add continuation if needed.
        parsed_items: list[tuple[str, bool, PlaylistEntry]] = []
        for item in contents:
            if not cls._is_json_object(item):
                continue
            renderer = item.get("playlistPanelVideoRenderer")
            if not cls._is_json_object(renderer) or "unplayableText" in renderer:
                continue
            vid = renderer.get("videoId")
            if not isinstance(vid, str) or not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(vid):
                continue
            canonical_url = f"https://www.youtube.com/watch?v={vid}"
            try:
                source_url = cls._url_adapter.validate_python(canonical_url)
            except ValidationError:
                continue
            title = cls._extract_text(renderer.get("title")) or canonical_url
            is_selected = renderer.get("selected") is True
            parsed_items.append(
                (vid, is_selected, PlaylistEntry(source_url=source_url, title=title))
            )

        if not parsed_items:
            return None

        start_index = 0
        if video_id is not None:
            for idx, (vid, is_selected, _) in enumerate(parsed_items):
                if is_selected or vid == video_id:
                    start_index = idx
                    break

        entries = tuple(
            entry
            for _, _, entry in parsed_items[start_index : start_index + max_tracks]
        )
        if not entries:
            return None
        return ResolvedPlaylist(
            title=cls._extract_text(playlist.get("title")),
            entries=entries,
        )

    async def resolve(self, url: HttpUrl) -> ResolvedPlaylist | None:
        parsed = self.parse_url(url)
        if parsed is None:
            return None
        playlist_id, video_id = parsed
        body: dict[str, object] = {
            "context": YOUTUBE_CLIENT_CONTEXT,
            "playlistId": playlist_id,
        }
        if video_id is not None:
            body["videoId"] = video_id

        try:
            async with ClientSession(timeout=self._timeout) as session:
                async with session.post(self._next_url, json=body) as response:
                    response.raise_for_status()
                    data: object = await response.json()
        except (ClientError, TimeoutError, ValueError) as exc:
            raise VoiceError("Could not load tracks from this playlist.") from exc

        resolved = self.extract_from_next(
            data, video_id=video_id, max_tracks=self._max_tracks
        )
        if resolved is None:
            raise VoiceError("Could not load tracks from this playlist.")
        return resolved

"""Resolve a text query to ranked YouTube video URLs via InnerTube."""

import logging
from collections.abc import Mapping
from typing import TypeIs

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import HttpUrl, PositiveFloat, PositiveInt, TypeAdapter

from milihu.exceptions.voice import VoiceError
from milihu.services.youtube_playlist import YOUTUBE_VIDEO_ID_PATTERN
from milihu.types.default.search import (
    DEFAULT_MAX_SEARCH_RESULTS,
    DEFAULT_SEARCH_TIMEOUT_SECONDS,
    YOUTUBE_MUSIC_SEARCH_ENDPOINT,
    YOUTUBE_WEB_SEARCH_ENDPOINT,
)
from milihu.types.search import SearchCandidates, YouTubeSearchEndpoint


class YouTubeSearchResolver:
    """Search YouTube Music first, falling back to YouTube Web."""

    _url_adapter: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

    def __init__(
        self,
        *,
        timeout_seconds: PositiveFloat = DEFAULT_SEARCH_TIMEOUT_SECONDS,
        max_results: PositiveInt = DEFAULT_MAX_SEARCH_RESULTS,
        music_endpoint: YouTubeSearchEndpoint = YOUTUBE_MUSIC_SEARCH_ENDPOINT,
        web_endpoint: YouTubeSearchEndpoint = YOUTUBE_WEB_SEARCH_ENDPOINT,
    ) -> None:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        self._timeout = ClientTimeout(total=timeout_seconds)
        self._max_results = max_results
        self._music_endpoint = music_endpoint
        self._web_endpoint = web_endpoint
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _is_object(value: object) -> TypeIs[Mapping[str, object]]:
        return isinstance(value, Mapping)

    @staticmethod
    def _is_list(value: object) -> TypeIs[list[object]]:
        return isinstance(value, list)

    @classmethod
    def _field(cls, value: object, key: str) -> object:
        return value.get(key) if cls._is_object(value) else None

    @classmethod
    def _path(cls, value: object, *keys: str) -> object:
        current = value
        for key in keys:
            current = cls._field(current, key)
        return current

    @classmethod
    def _valid_video_id(cls, value: object) -> str | None:
        if isinstance(value, str) and YOUTUBE_VIDEO_ID_PATTERN.fullmatch(value):
            return value
        return None

    @classmethod
    def _runs_video_id(cls, text_obj: object) -> str | None:
        runs = cls._field(text_obj, "runs")
        if not cls._is_list(runs):
            return None
        for run in runs:
            vid = cls._valid_video_id(
                cls._path(run, "navigationEndpoint", "watchEndpoint", "videoId")
            )
            if vid is not None:
                return vid
        return None

    @classmethod
    def _music_card_video_id(cls, card: object) -> str | None:
        direct = cls._valid_video_id(
            cls._path(card, "onTap", "watchEndpoint", "videoId")
        )
        if direct is not None:
            return direct
        return cls._runs_video_id(cls._field(card, "title"))

    @classmethod
    def _music_item_video_id(cls, item: object) -> str | None:
        renderer = cls._field(item, "musicResponsiveListItemRenderer")
        if not cls._is_object(renderer):
            return None
        direct = cls._valid_video_id(cls._path(renderer, "playlistItemData", "videoId"))
        if direct is not None:
            return direct
        overlay_vid = cls._valid_video_id(
            cls._path(
                renderer,
                "overlay",
                "musicItemThumbnailOverlayRenderer",
                "content",
                "musicPlayButtonRenderer",
                "playNavigationEndpoint",
                "watchEndpoint",
                "videoId",
            )
        )
        if overlay_vid is not None:
            return overlay_vid
        columns = cls._field(renderer, "flexColumns")
        if not cls._is_list(columns):
            return None
        for column in columns:
            text_obj = cls._path(
                column, "musicResponsiveListItemFlexColumnRenderer", "text"
            )
            vid = cls._runs_video_id(text_obj)
            if vid is not None:
                return vid
        return None

    @classmethod
    def _collect_urls(
        cls, video_ids: list[str], *, limit: int
    ) -> SearchCandidates:
        urls: list[HttpUrl] = []
        seen: set[str] = set()
        for vid in video_ids:
            if vid in seen:
                continue
            seen.add(vid)
            urls.append(
                cls._url_adapter.validate_python(
                    f"https://www.youtube.com/watch?v={vid}"
                )
            )
            if len(urls) >= limit:
                break
        return tuple(urls)

    @classmethod
    def music_video_urls(
        cls, payload: object, *, limit: int = DEFAULT_MAX_SEARCH_RESULTS
    ) -> SearchCandidates:
        """Extract ranked watch URLs from a YouTube Music ``WEB_REMIX`` response."""
        tabs = cls._path(payload, "contents", "tabbedSearchResultsRenderer", "tabs")
        if not cls._is_list(tabs):
            return ()

        selected_tab: object = None
        fallback_tab: object = None
        for tab in tabs:
            renderer = cls._field(tab, "tabRenderer")
            if not cls._is_object(renderer):
                continue
            if renderer.get("selected") is True:
                selected_tab = renderer
                break
            if fallback_tab is None and "content" in renderer:
                fallback_tab = renderer

        tab_renderer = selected_tab if selected_tab is not None else fallback_tab
        sections = cls._path(tab_renderer, "content", "sectionListRenderer", "contents")
        if not cls._is_list(sections):
            return ()

        video_ids: list[str] = []
        deferred_card_ids: list[str] = []
        for section in sections:
            card = cls._field(section, "musicCardShelfRenderer")
            if cls._is_object(card):
                card_vid = cls._music_card_video_id(card)
                nested = cls._field(card, "contents")
                if card_vid is not None:
                    video_ids.append(card_vid)
                    if cls._is_list(nested):
                        for entry in nested:
                            vid = cls._music_item_video_id(entry)
                            if vid is not None:
                                deferred_card_ids.append(vid)
                elif cls._is_list(nested):
                    for entry in nested:
                        vid = cls._music_item_video_id(entry)
                        if vid is not None:
                            video_ids.append(vid)
                continue

            for key in ("itemSectionRenderer", "musicShelfRenderer"):
                items = cls._path(section, key, "contents")
                if not cls._is_list(items):
                    continue
                for item in items:
                    vid = cls._music_item_video_id(item)
                    if vid is not None:
                        video_ids.append(vid)

        video_ids.extend(deferred_card_ids)
        return cls._collect_urls(video_ids, limit=limit)

    @classmethod
    def _web_video_id(cls, item: object) -> str | None:
        video = cls._field(item, "videoRenderer")
        if not cls._is_object(video) or "upcomingEventData" in video:
            return None
        return cls._valid_video_id(video.get("videoId"))

    @classmethod
    def web_video_urls(
        cls, payload: object, *, limit: int = DEFAULT_MAX_SEARCH_RESULTS
    ) -> SearchCandidates:
        """Extract ranked watch URLs from a YouTube ``WEB`` search response."""
        sections = cls._path(
            payload,
            "contents",
            "twoColumnSearchResultsRenderer",
            "primaryContents",
            "sectionListRenderer",
            "contents",
        )
        if not cls._is_list(sections):
            return ()

        video_ids: list[str] = []
        for section in sections:
            items = cls._path(section, "itemSectionRenderer", "contents")
            if not cls._is_list(items):
                continue
            for item in items:
                vid = cls._web_video_id(item)
                if vid is not None:
                    video_ids.append(vid)
                    continue
                shelf_items = cls._path(
                    item, "shelfRenderer", "content", "verticalListRenderer", "items"
                )
                if cls._is_list(shelf_items):
                    for shelf_item in shelf_items:
                        shelf_vid = cls._web_video_id(shelf_item)
                        if shelf_vid is not None:
                            video_ids.append(shelf_vid)
        return cls._collect_urls(video_ids, limit=limit)

    @classmethod
    def _is_age_restricted_search(cls, payload: object) -> bool:
        sections = cls._path(
            payload,
            "contents",
            "twoColumnSearchResultsRenderer",
            "primaryContents",
            "sectionListRenderer",
            "contents",
        )
        if not cls._is_list(sections):
            return False
        for section in sections:
            items = cls._path(section, "itemSectionRenderer", "contents")
            if not cls._is_list(items):
                continue
            for item in items:
                promo = cls._field(item, "backgroundPromoRenderer")
                if (
                    cls._is_object(promo)
                    and cls._path(promo, "icon", "iconType") == "EMPTY_SEARCH"
                    and cls._field(promo, "ctaButton") is not None
                ):
                    return True
        return False

    async def _post_search(
        self,
        session: ClientSession,
        endpoint: YouTubeSearchEndpoint,
        query: str,
    ) -> object:
        body: dict[str, object] = {
            "context": endpoint.context.model_dump(),
            "query": query,
        }
        headers = dict(endpoint.headers) if endpoint.headers else None
        async with session.post(
            str(endpoint.url), json=body, headers=headers
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def search(self, query: str) -> SearchCandidates:
        async with ClientSession(timeout=self._timeout) as session:
            try:
                music_payload = await self._post_search(
                    session, self._music_endpoint, query
                )
            except (ClientError, TimeoutError, ValueError) as exc:
                self._logger.debug(
                    "YouTube Music search failed for %r, falling back to Web: %s",
                    query,
                    exc,
                )
            else:
                music_urls = self.music_video_urls(
                    music_payload, limit=self._max_results
                )
                if music_urls:
                    return music_urls
                self._logger.debug(
                    "YouTube Music search returned no videos for %r; "
                    "falling back to Web",
                    query,
                )

            try:
                web_payload = await self._post_search(
                    session, self._web_endpoint, query
                )
            except (ClientError, TimeoutError, ValueError) as exc:
                raise VoiceError("Could not search YouTube right now.") from exc

        web_urls = self.web_video_urls(web_payload, limit=self._max_results)
        if web_urls:
            return web_urls
        if self._is_age_restricted_search(web_payload):
            raise VoiceError("YouTube restricted this search for signed-out users.")
        raise VoiceError("No videos found for this search.")

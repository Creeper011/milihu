import unittest
from typing import Any, Self
from unittest.mock import patch

from aiohttp import ClientError
from pydantic import HttpUrl, TypeAdapter

from milihu.exceptions.voice import VoiceError
from milihu.services.youtube_playlist import YouTubePlaylistResolver

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def _url(raw: str) -> HttpUrl:
    return _URL_ADAPTER.validate_python(raw)


def _next_payload(
    items: list[dict[str, Any]],
    *,
    title: Any = "Mix - Indie music",
) -> dict[str, Any]:
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "playlist": {
                    "playlist": {
                        "title": title,
                        "contents": items,
                    }
                }
            }
        }
    }


def _renderer(
    video_id: str,
    title: Any,
    *,
    selected: bool = False,
    unplayable: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "videoId": video_id,
        "title": title,
        "selected": selected,
    }
    if unplayable:
        body["unplayableText"] = {"simpleText": "Video unavailable"}
    return {"playlistPanelVideoRenderer": body}


class _FakeResponse:
    def __init__(
        self,
        data: Any = None,
        *,
        status_error: Exception | None = None,
    ) -> None:
        self._data = data
        self._status_error = status_error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    async def json(self) -> Any:
        return self._data


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.requests.append((url, json))
        return self.response


class YouTubePlaylistTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_youtube_playlist_urls(self) -> None:
        self.assertEqual(
            YouTubePlaylistResolver.parse_url(
                _url("https://youtu.be/d0vU4xThRWI?list=RDGMEM6ijAnFTG9nX1G-kbWBUCJA")
            ),
            ("RDGMEM6ijAnFTG9nX1G-kbWBUCJA", "d0vU4xThRWI"),
        )
        self.assertEqual(
            YouTubePlaylistResolver.parse_url(
                _url("https://www.youtube.com/watch?v=abc_123&list=PLxyz")
            ),
            ("PLxyz", "abc_123"),
        )
        self.assertEqual(
            YouTubePlaylistResolver.parse_url(
                _url("https://music.youtube.com/playlist?list=OLAK5uy_abc")
            ),
            ("OLAK5uy_abc", None),
        )
        self.assertIsNone(
            YouTubePlaylistResolver.parse_url(_url("https://youtu.be/d0vU4xThRWI"))
        )
        self.assertIsNone(
            YouTubePlaylistResolver.parse_url(
                _url("https://example.com/watch?v=a&list=PL123")
            )
        )

    def test_extract_playlist_filters_unplayable_slices_selected_and_caps(
        self,
    ) -> None:
        payload = _next_payload(
            [
                _renderer("vid1", {"simpleText": "Track 1"}),
                _renderer(
                    "vid2",
                    {"runs": [{"text": "Track "}, {"text": "2"}]},
                    selected=True,
                ),
                _renderer("dead", {"simpleText": "Deleted"}, unplayable=True),
                _renderer("vid3", {"simpleText": "Track 3"}),
                _renderer("vid4", {"simpleText": "Track 4"}),
            ],
            title="My Playlist",
        )
        resolved = YouTubePlaylistResolver.extract_from_next(
            payload, video_id="vid2", max_tracks=2
        )
        assert resolved is not None
        self.assertEqual(resolved.title, "My Playlist")
        self.assertEqual(
            [(str(e.source_url), e.title) for e in resolved.entries],
            [
                ("https://www.youtube.com/watch?v=vid2", "Track 2"),
                ("https://www.youtube.com/watch?v=vid3", "Track 3"),
            ],
        )

        # Malformed or null JSON nodes return None safely.
        self.assertIsNone(
            YouTubePlaylistResolver.extract_from_next({"contents": None})
        )

    async def test_resolver_returns_none_for_non_playlist_and_raises_on_failure(
        self,
    ) -> None:
        resolver = YouTubePlaylistResolver(max_tracks=10)

        # Non-playlist URL returns None without HTTP calls.
        self.assertIsNone(await resolver.resolve(_url("https://youtu.be/d0vU4xThRWI")))

        # Valid playlist returns ResolvedPlaylist and sends videoId + playlistId.
        ok_session = _FakeSession(
            _FakeResponse(
                _next_payload(
                    [_renderer("d0vU4xThRWI", {"simpleText": "Imposter Syndrome"})]
                )
            )
        )
        with patch(
            "milihu.services.youtube_playlist.ClientSession",
            return_value=ok_session,
        ):
            result = await resolver.resolve(
                _url("https://youtu.be/d0vU4xThRWI?list=RDGMEM6ijAnFTG9nX1G-kbWBUCJA")
            )
        assert result is not None
        self.assertEqual(result.title, "Mix - Indie music")
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(ok_session.requests[0][1]["videoId"], "d0vU4xThRWI")
        self.assertEqual(
            ok_session.requests[0][1]["playlistId"], "RDGMEM6ijAnFTG9nX1G-kbWBUCJA"
        )

        # Empty playlist raises VoiceError (no fallback logic in adapter).
        empty_session = _FakeSession(_FakeResponse({"contents": {}}))
        with (
            patch(
                "milihu.services.youtube_playlist.ClientSession",
                return_value=empty_session,
            ),
            self.assertRaisesRegex(VoiceError, "playlist"),
        ):
            await resolver.resolve(
                _url("https://www.youtube.com/watch?v=d0vU4xThRWI&list=WL")
            )

        # Network/HTTP failure raises VoiceError.
        err_session = _FakeSession(_FakeResponse(status_error=ClientError("boom")))
        with (
            patch(
                "milihu.services.youtube_playlist.ClientSession",
                return_value=err_session,
            ),
            self.assertRaisesRegex(VoiceError, "playlist"),
        ):
            await resolver.resolve(
                _url("https://www.youtube.com/playlist?list=PLprivate")
            )

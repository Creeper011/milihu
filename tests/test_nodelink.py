import asyncio
import unittest
from typing import Self
from unittest.mock import patch

from aiohttp import ClientError
from pydantic import HttpUrl, TypeAdapter

from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.services.nodelink.client import NodeLinkClient
from milihu.types.playlist import AudioTrackItem

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def _url(raw: str) -> HttpUrl:
    return _URL_ADAPTER.validate_python(raw)


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        json_data: object = None,
        text_data: str = "3.9.0",
    ) -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self) -> object:
        await asyncio.sleep(0)
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    async def text(self) -> str:
        await asyncio.sleep(0)
        return self._text_data


class FakeSession:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse | Exception]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> FakeResponse:
        param_key = ""
        if params:
            param_key = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        key = (path, param_key)
        self.requests.append(key)
        outcome = self.routes.get(key) or self.routes.get((path, "*"))
        if outcome is None:
            raise AssertionError(f"Unexpected GET {key}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


class NodeLinkClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_spotify_playlist_and_format_author_title(self) -> None:
        playlist_url = _url(
            "https://open.spotify.com/playlist/40GRBpPK3knyYNfPFAlGlG"
        )
        session = FakeSession(
            {
                ("/version", ""): FakeResponse(text_data="3.9.0"),
                (
                    "/v4/loadtracks",
                    f"identifier={playlist_url}",
                ): FakeResponse(
                    json_data={
                        "loadType": "playlist",
                        "data": {
                            "info": {
                                "name": "sophia hipotética",
                                "selectedTrack": 1,
                            },
                            "tracks": [
                                {
                                    "encoded": "enc-1",
                                    "info": {
                                        "title": "Love Me Not",
                                        "author": "Ravyn Lenae",
                                        "uri": "https://open.spotify.com/track/1UNEuG9DYOWiikf00ayr52",
                                        "isStream": False,
                                    },
                                },
                                {
                                    "encoded": "enc-2",
                                    "info": {
                                        "title": "Looking Out for You",
                                        "author": "Joy Again",
                                        "uri": "https://open.spotify.com/track/3jfZ9M23l0L7RxzYMTgBTv",
                                        "isStream": False,
                                    },
                                },
                                {
                                    "encoded": "enc-3",
                                    "info": {
                                        "title": "Malcolm Todd - Sweet Boy",
                                        "author": "Malcolm Todd",
                                        "uri": "https://open.spotify.com/track/59c2xv2kMzYM6HR9oY6BIa",
                                        "isStream": False,
                                    },
                                },
                            ],
                        },
                    }
                ),
            }
        )
        with patch(
            "milihu.services.nodelink.client.ClientSession", return_value=session
        ):
            async with NodeLinkClient(
                "http://127.0.0.1:2333", "secret-pw", max_playlist_tracks=2
            ) as client:
                loaded = await client.load(playlist_url)

        self.assertEqual(loaded.kind, "playlist")
        assert loaded.playlist is not None
        self.assertEqual(loaded.playlist.title, "sophia hipotética")
        # Starts at selectedTrack=1 and caps at max_playlist_tracks=2.
        self.assertEqual(
            [(e.title, e.encoded) for e in loaded.playlist.entries],
            [
                ("Joy Again - Looking Out for You", "enc-2"),
                ("Malcolm Todd - Sweet Boy", "enc-3"),
            ],
        )
        self.assertTrue(session.closed)

    async def test_search_prefers_ytmsearch_and_falls_back_to_ytsearch(self) -> None:
        session = FakeSession(
            {
                ("/version", ""): FakeResponse(text_data="3.9.0"),
                (
                    "/v4/loadtracks",
                    "identifier=ytmsearch:rare song",
                ): FakeResponse(json_data={"loadType": "empty", "data": {}}),
                (
                    "/v4/loadtracks",
                    "identifier=ytsearch:rare song",
                ): FakeResponse(
                    json_data={
                        "loadType": "search",
                        "data": [
                            {
                                "encoded": "yt-1",
                                "info": {
                                    "title": "Rare Song",
                                    "author": "Unknown artist",
                                    "uri": "https://www.youtube.com/watch?v=abc123",
                                    "isStream": False,
                                },
                            },
                            {
                                "encoded": "yt-2",
                                "info": {
                                    "title": "Second Result",
                                    "author": "Band",
                                    "uri": "https://www.youtube.com/watch?v=def456",
                                    "isStream": False,
                                },
                            },
                        ],
                    }
                ),
            }
        )
        with patch(
            "milihu.services.nodelink.client.ClientSession", return_value=session
        ):
            async with NodeLinkClient(
                "http://127.0.0.1:2333", "secret-pw", max_search_results=1
            ) as client:
                results = await client.search("  rare song  ")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Rare Song")
        self.assertEqual(results[0].encoded, "yt-1")
        self.assertEqual(
            session.requests[1:],
            [
                ("/v4/loadtracks", "identifier=ytmsearch:rare song"),
                ("/v4/loadtracks", "identifier=ytsearch:rare song"),
            ],
        )

    async def test_resolve_stream_validates_trackstream_and_attaches_auth_header(
        self,
    ) -> None:
        item = AudioTrackItem(
            source_url=_url(
                "https://open.spotify.com/track/1UNEuG9DYOWiikf00ayr52"
            ),
            title="Ravyn Lenae - Love Me Not",
            encoded="enc+track/1==",
            is_live=False,
        )
        session = FakeSession(
            {
                ("/version", ""): FakeResponse(text_data="3.9.0"),
                (
                    "/v4/trackstream",
                    "encodedTrack=enc+track/1==",
                ): FakeResponse(
                    json_data={
                        "url": "https://mirror.example/audio",
                        "protocol": "https",
                    }
                ),
            }
        )
        with patch(
            "milihu.services.nodelink.client.ClientSession", return_value=session
        ):
            async with NodeLinkClient(
                "http://127.0.0.1:2333", "ephemeral-secret"
            ) as client:
                stream = await client.resolve_stream(item)

        self.assertEqual(
            stream.stream_url,
            "http://127.0.0.1:2333/v4/loadstream"
            "?encodedTrack=enc%2Btrack%2F1%3D%3D&format=raw",
        )
        self.assertEqual(stream.title, "Ravyn Lenae - Love Me Not")
        self.assertFalse(stream.is_direct)
        self.assertEqual(stream.headers, (("Authorization", "ephemeral-secret"),))

    async def test_error_classification_never_leaks_localhost_url(self) -> None:
        item = AudioTrackItem(
            source_url=_url("https://www.youtube.com/watch?v=dead"),
            title="Dead Track",
            encoded="enc-dead",
        )
        # Track-level 500 from trackstream raises VoiceError without localhost URL.
        session_track_err = FakeSession(
            {
                ("/version", ""): FakeResponse(text_data="3.9.0"),
                (
                    "/v4/trackstream",
                    "encodedTrack=enc-dead",
                ): FakeResponse(
                    status=500,
                    json_data={"status": 500, "message": "This video is unavailable"},
                ),
            }
        )
        with patch(
            "milihu.services.nodelink.client.ClientSession",
            return_value=session_track_err,
        ):
            async with NodeLinkClient("http://127.0.0.1:2333", "pw") as client:
                with self.assertRaises(VoiceError) as ctx:
                    await client.resolve_stream(item)
                self.assertNotIsInstance(ctx.exception, BackendUnavailableError)
                self.assertIn("This video is unavailable", str(ctx.exception))
                self.assertNotIn("127.0.0.1", str(ctx.exception))

        # Connection failure raises BackendUnavailableError without localhost URL.
        session_outage = FakeSession(
            {
                ("/version", ""): FakeResponse(text_data="3.9.0"),
                ("/v4/trackstream", "*"): ClientError("connection reset"),
            }
        )
        with patch(
            "milihu.services.nodelink.client.ClientSession",
            return_value=session_outage,
        ):
            async with NodeLinkClient("http://127.0.0.1:2333", "pw") as client:
                with self.assertRaises(BackendUnavailableError) as ctx2:
                    await client.resolve_stream(item)
                self.assertNotIn("127.0.0.1", str(ctx2.exception))

    async def test_rejects_requests_outside_lifecycle_and_invalid_limits(self) -> None:
        with self.assertRaises(ValueError):
            NodeLinkClient("http://127.0.0.1:2333", "pw", max_playlist_tracks=0)
        with self.assertRaises(ValueError):
            NodeLinkClient("http://127.0.0.1:2333", "pw", max_search_results=0)

        client = NodeLinkClient("http://127.0.0.1:2333", "pw")
        with self.assertRaises(RuntimeError):
            await client.load(_url("https://example.com"))
        with self.assertRaises(RuntimeError):
            await client.search("hello")

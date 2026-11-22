import asyncio
import socket
import unittest
from typing import Any
from unittest.mock import patch

from pydantic import HttpUrl, TypeAdapter

from milihu.exceptions.voice import VoiceError
from milihu.services.http.stream_probe import StreamProbe
from milihu.types.voice import ProbedAudio, StreamMetadata

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def _addrinfo_for(*ips: str) -> list[Any]:
    return [
        (
            socket.AF_INET6 if ":" in ip else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (ip, 80),
        )
        for ip in ips
    ]


class FakeContent:
    def __init__(self, payload: bytes) -> None:
        self._buf = payload
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        chunk = self._buf[self._pos : self._pos + n]
        if len(chunk) < n:
            raise asyncio.IncompleteReadError(chunk, n)
        self._pos += n
        return chunk


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        content_type: str = "audio/mpeg",
        headers: dict[str, str] | None = None,
        content_length: int | None = None,
        payload: bytes = b"",
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self.content_length = content_length
        self.content = FakeContent(payload)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[str, dict[str, str], bool]] = []
        self.closed = False

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        allow_redirects: bool,
    ) -> FakeResponse:
        self.calls.append((url, headers, allow_redirects))
        return next(self._responses)

    async def close(self) -> None:
        self.closed = True


class StreamProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_url_blocks_non_http_and_non_global_ips(self) -> None:
        probe = StreamProbe()

        for bad_url in (
            "file:///etc/passwd",
            "ftp://example.com/a.mp3",
            "gopher://example.com/1",
            "http:///no-host",
        ):
            with self.assertRaises(VoiceError):
                await probe.validate_url(bad_url)

        restricted_ips = [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.5",
            "169.254.169.254",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:127.0.0.1",
        ]
        loop = asyncio.get_running_loop()
        for ip in restricted_ips:
            async def _fake_resolve(
                host: Any, port: Any, *, _ip: str = ip, **_: Any
            ) -> list[Any]:
                return _addrinfo_for(_ip)

            with patch.object(loop, "getaddrinfo", side_effect=_fake_resolve):
                with self.assertRaisesRegex(VoiceError, "restricted"):
                    await probe.validate_url("http://radio.example/stream")

        # Mixed public + private DNS response must still be rejected.
        async def _mixed_resolve(*_: Any, **__: Any) -> list[Any]:
            return _addrinfo_for("93.184.216.34", "10.0.0.2")

        with patch.object(loop, "getaddrinfo", side_effect=_mixed_resolve):
            with self.assertRaisesRegex(VoiceError, "restricted"):
                await probe.validate_url("http://dual.example/stream")

        # Public global IP passes.
        async def _public_resolve(*_: Any, **__: Any) -> list[Any]:
            return _addrinfo_for("93.184.216.34")

        with patch.object(loop, "getaddrinfo", side_effect=_public_resolve):
            await probe.validate_url("https://public.example/stream")

        await probe.aclose()

    async def test_redirect_loop_validates_every_hop_and_bounds_depth(self) -> None:
        probe = StreamProbe(max_redirects=2)
        loop = asyncio.get_running_loop()

        # Public -> private redirect is blocked and redirect response is closed.
        redir = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/secret"})
        session = FakeSession([redir])

        async def _resolve(host: str, port: Any, **_: Any) -> list[Any]:
            if host == "127.0.0.1":
                return _addrinfo_for("127.0.0.1")
            return _addrinfo_for("93.184.216.34")

        with (
            patch.object(loop, "getaddrinfo", side_effect=_resolve),
            patch(
                "milihu.services.http.stream_probe.ClientSession",
                return_value=session,
            ),
        ):
            with self.assertRaisesRegex(VoiceError, "restricted"):
                await probe.probe_audio(
                    _URL_ADAPTER.validate_python("https://public.example/start")
                )
        self.assertTrue(redir.closed)
        self.assertEqual(session.calls[0][2], False)  # allow_redirects=False

        # Too many redirects raises VoiceError.
        loop_responses = [
            FakeResponse(status=302, headers={"Location": f"https://public.example/{i}"})
            for i in range(4)
        ]
        probe2 = StreamProbe(max_redirects=2)
        session2 = FakeSession(loop_responses)
        with (
            patch.object(loop, "getaddrinfo", side_effect=_resolve),
            patch(
                "milihu.services.http.stream_probe.ClientSession",
                return_value=session2,
            ),
        ):
            with self.assertRaisesRegex(VoiceError, "Too many redirects"):
                await probe2.probe_audio(
                    _URL_ADAPTER.validate_python("https://public.example/0")
                )

        # Valid public redirect follows to final URL.
        probe3 = StreamProbe(max_redirects=2)
        final_resp = FakeResponse(
            status=200,
            content_type="audio/mpeg",
            headers={"icy-name": "Moved Radio"},
        )
        session3 = FakeSession(
            [
                FakeResponse(
                    status=301,
                    headers={"Location": "https://cdn.example/live.mp3"},
                ),
                final_resp,
            ]
        )
        with (
            patch.object(loop, "getaddrinfo", side_effect=_resolve),
            patch(
                "milihu.services.http.stream_probe.ClientSession",
                return_value=session3,
            ),
        ):
            res = await probe3.probe_audio(
                _URL_ADAPTER.validate_python("https://public.example/radio")
            )
        self.assertEqual(
            res,
            ProbedAudio(
                stream_url="https://cdn.example/live.mp3",
                title="Moved Radio",
                is_live=True,
            ),
        )
        self.assertTrue(final_resp.closed)

        await probe.aclose()
        await probe2.aclose()
        await probe3.aclose()
        self.assertTrue(session.closed)

    async def test_probe_unquotes_path_excludes_playlists_and_reuses_session(
        self,
    ) -> None:
        probe = StreamProbe()
        loop = asyncio.get_running_loop()

        async def _public_resolve(*_: Any, **__: Any) -> list[Any]:
            return _addrinfo_for("93.184.216.34")

        responses = [
            FakeResponse(content_type="audio/mpeg", content_length=12345),
            FakeResponse(content_type="audio/x-scpls"),
            FakeResponse(content_type="audio/x-mpegurl"),
            FakeResponse(
                content_type="audio/mpeg",
                headers={"icy-metaint": "4"},
                payload=b"abcd\x01StreamTitle='A';\x00",
            ),
        ]
        session = FakeSession(responses)
        with (
            patch.object(loop, "getaddrinfo", side_effect=_public_resolve),
            patch(
                "milihu.services.http.stream_probe.ClientSession",
                return_value=session,
            ) as session_cls,
        ):
            r1 = await probe.probe_audio(
                _URL_ADAPTER.validate_python(
                    "https://cdn.example/My%20Great%20Track.mp3"
                )
            )
            r2 = await probe.probe_audio(
                _URL_ADAPTER.validate_python("https://cdn.example/station.pls")
            )
            r3 = await probe.probe_audio(
                _URL_ADAPTER.validate_python("https://cdn.example/station.m3u")
            )
            icy_meta = await probe.fetch_icy_title("https://cdn.example/live")

        self.assertEqual(
            r1,
            ProbedAudio(
                stream_url="https://cdn.example/My%20Great%20Track.mp3",
                title="My Great Track",
                is_live=False,
            ),
        )
        self.assertIsNone(r2)
        self.assertIsNone(r3)
        self.assertEqual(icy_meta, StreamMetadata(supported=True, title="A"))
        # Shared ClientSession created only once across all calls.
        self.assertEqual(session_cls.call_count, 1)

        await probe.aclose()
        self.assertTrue(session.closed)

    async def test_fetch_icy_title_stops_on_ssrf_redirect(self) -> None:
        probe = StreamProbe()
        loop = asyncio.get_running_loop()

        async def _resolve(host: str, port: Any, **_: Any) -> list[Any]:
            if host == "169.254.169.254":
                return _addrinfo_for("169.254.169.254")
            return _addrinfo_for("93.184.216.34")

        session = FakeSession(
            [
                FakeResponse(
                    status=302,
                    headers={"Location": "http://169.254.169.254/latest/meta-data"},
                )
            ]
        )
        with (
            patch.object(loop, "getaddrinfo", side_effect=_resolve),
            patch(
                "milihu.services.http.stream_probe.ClientSession",
                return_value=session,
            ),
        ):
            result = await probe.fetch_icy_title("https://radio.example/stream")
        self.assertEqual(result, StreamMetadata(supported=False))
        await probe.aclose()

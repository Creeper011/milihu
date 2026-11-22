"""SSRF-safe HTTP direct-audio probing and ICY metadata adapter."""

import asyncio
import ipaddress
import re
import socket
from pathlib import PurePath
from urllib.parse import unquote, urljoin, urlsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from pydantic import HttpUrl

from milihu.exceptions.voice import VoiceError
from milihu.types.default.voice import DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS
from milihu.types.voice import ProbedAudio, StreamMetadata

MAX_ICY_METAINT_BYTES = 256 * 1024

HLS_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    }
)
DIRECT_AUDIO_CONTENT_TYPES: frozenset[str] = HLS_CONTENT_TYPES | {"application/ogg"}
EXCLUDED_AUDIO_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "audio/x-scpls",
        "audio/x-mpegurl",
    }
)
GENERIC_STREAM_PATH_NAMES: frozenset[str] = frozenset(
    {"stream", "live", "radio", "listen"}
)
REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})
MAX_STREAM_REDIRECTS = 5


def _is_public_ip(raw_ip: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw_ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


class StreamProbe:
    """Validate stream URLs against SSRF, probe direct audio, and read ICY titles."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_STREAM_PROBE_TIMEOUT_SECONDS,
        max_redirects: int = MAX_STREAM_REDIRECTS,
        max_icy_metaint_bytes: int = MAX_ICY_METAINT_BYTES,
    ) -> None:
        self._timeout = ClientTimeout(total=timeout_seconds)
        self._max_redirects = max_redirects
        self._max_icy_metaint_bytes = max_icy_metaint_bytes
        self._session: ClientSession | None = None

    def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def validate_url(self, url: str) -> None:
        """Ensure ``url`` uses HTTP(S) and resolves only to globally routable IPs."""
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise VoiceError("Stream URL must use HTTP or HTTPS.")
        host = parsed.hostname
        if not host:
            raise VoiceError("Stream URL has no host.")
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise VoiceError("Stream URL has an invalid port.") from exc

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise VoiceError("Could not resolve stream host.") from exc
        if not infos:
            raise VoiceError("Could not resolve stream host.")

        for info in infos:
            addr = info[4][0]
            if not _is_public_ip(addr):
                raise VoiceError("Stream URL points to a restricted address.")

    async def _open_safe(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[str, ClientResponse]:
        current_url = url
        session = self._get_session()
        for _ in range(self._max_redirects + 1):
            await self.validate_url(current_url)
            response = await session.get(
                current_url,
                headers=headers,
                allow_redirects=False,
            )
            if response.status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise VoiceError("Redirect response missing Location header.")
                current_url = urljoin(current_url, location)
                continue
            return current_url, response
        raise VoiceError("Too many redirects while probing stream.")

    async def probe_audio(self, url: HttpUrl) -> ProbedAudio | None:
        """Probe ``url`` for direct audio and return structured stream info."""
        try:
            final_url, response = await self._open_safe(
                str(url), headers={"Icy-MetaData": "1"}
            )
        except (ClientError, TimeoutError):
            return None

        try:
            if response.status >= 400:
                return None
            content_type = response.content_type.lower()
            if content_type in EXCLUDED_AUDIO_CONTENT_TYPES or not (
                content_type.startswith("audio/")
                or content_type in DIRECT_AUDIO_CONTENT_TYPES
            ):
                return None
            icy_name = (response.headers.get("icy-name") or "").strip()
            is_live = (
                bool(icy_name)
                or response.content_length is None
                or content_type in HLS_CONTENT_TYPES
            )
            path_stem = PurePath(unquote(url.path or "")).stem.strip()
            fallback_title = (
                path_stem
                if path_stem and path_stem.lower() not in GENERIC_STREAM_PATH_NAMES
                else (url.host or str(url))
            )
            return ProbedAudio(
                stream_url=final_url,
                title=icy_name or fallback_title,
                is_live=is_live,
            )
        finally:
            # Drop the socket immediately instead of draining a live stream.
            response.close()

    @staticmethod
    def parse_icy_stream_title(raw: bytes) -> str | None:
        text = raw.decode("utf-8", errors="replace").rstrip("\x00")
        match = re.search(r"StreamTitle='(.*?)'(?:;|$)", text)
        if match is None:
            return None
        title = match.group(1).strip()
        return title or None

    async def fetch_icy_title(self, stream_url: str) -> StreamMetadata:
        """Read one ICY metadata frame and return structured metadata."""
        try:
            _, response = await self._open_safe(
                stream_url, headers={"Icy-MetaData": "1"}
            )
        except VoiceError:
            return StreamMetadata(supported=False)
        except (ClientError, TimeoutError):
            return StreamMetadata(supported=True)

        try:
            if response.status >= 400:
                return StreamMetadata(supported=False)
            metaint_raw = (response.headers.get("icy-metaint") or "").strip()
            if not metaint_raw.isdigit():
                return StreamMetadata(supported=False)
            metaint = int(metaint_raw)
            if metaint <= 0 or metaint > self._max_icy_metaint_bytes:
                return StreamMetadata(supported=False)
            await response.content.readexactly(metaint)
            length_byte = await response.content.readexactly(1)
            meta_len = length_byte[0] * 16
            if meta_len == 0:
                return StreamMetadata(supported=True)
            meta_bytes = await response.content.readexactly(meta_len)
            return StreamMetadata(
                supported=True,
                title=self.parse_icy_stream_title(meta_bytes),
            )
        except (ClientError, TimeoutError, asyncio.IncompleteReadError):
            return StreamMetadata(supported=True)
        finally:
            response.close()

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

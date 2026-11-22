"""SSRF-safe remote media link resolver and fetcher (direct media + OG/GIF pages)."""

import asyncio
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePath
from typing import override
from urllib.parse import unquote, urljoin, urlsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from milihu.exceptions.payload import InputTooLargeError
from milihu.types.common import NonEmptyStr
from milihu.types.media import CAPTION_MEDIA_EXTENSIONS, MEDIA_PREFIXES

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 512 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; MilihuBot/1.0)"

MIME_TO_EXTENSION: dict[str, str] = {
    "image/gif": ".gif",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}

VIDEO_META_KEYS: tuple[str, ...] = (
    "og:video:secure_url",
    "og:video:url",
    "og:video",
    "twitter:player:stream",
)
IMAGE_META_KEYS: tuple[str, ...] = (
    "og:image:secure_url",
    "og:image:url",
    "og:image",
    "twitter:image:src",
    "twitter:image",
)
HTML_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm", ".php", ".aspx"})
GENERIC_STEMS: frozenset[str] = frozenset({"media", "video", "image", "gif", "file"})


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


class _OgMediaParser(HTMLParser):
    """Extract candidate video/GIF/image URLs from OpenGraph and video tags."""

    def __init__(self) -> None:
        super().__init__()
        self.meta_values: dict[str, list[str]] = {}
        self.video_sources: list[str] = []
        self._in_video = False

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.lower()
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs}
        if lower_tag == "meta":
            key = (attr_map.get("property") or attr_map.get("name") or "").lower()
            content = attr_map.get("content", "")
            if key and content:
                self.meta_values.setdefault(key, []).append(content)
        elif lower_tag == "video":
            self._in_video = True
            src = attr_map.get("src", "")
            if src:
                self.video_sources.append(src)
        elif lower_tag == "source" and self._in_video:
            src = attr_map.get("src", "")
            if src:
                self.video_sources.append(src)

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "video":
            self._in_video = False


def extract_media_url_from_html(html: str, base_url: str) -> str | None:
    """Return the best candidate media URL from HTML OpenGraph or video tags."""
    parser = _OgMediaParser()
    parser.feed(html)

    video_types = [t.lower() for t in parser.meta_values.get("og:video:type", ())]
    video_is_html = bool(video_types) and all("html" in t for t in video_types)

    def _resolve(candidate: str) -> str | None:
        resolved = urljoin(base_url, candidate)
        parsed = urlsplit(resolved)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None
        return resolved

    if not video_is_html:
        for key in VIDEO_META_KEYS:
            for candidate in parser.meta_values.get(key, ()):
                resolved = _resolve(candidate)
                if resolved is None:
                    continue
                suffix = PurePath(unquote(urlsplit(resolved).path)).suffix.lower()
                if suffix in HTML_EXTENSIONS:
                    continue
                return resolved

    for candidate in parser.video_sources:
        resolved = _resolve(candidate)
        if resolved is not None:
            return resolved

    image_candidates: list[str] = []
    for key in IMAGE_META_KEYS:
        for candidate in parser.meta_values.get(key, ()):
            resolved = _resolve(candidate)
            if resolved is not None:
                image_candidates.append(resolved)

    for candidate in image_candidates:
        suffix = PurePath(unquote(urlsplit(candidate).path)).suffix.lower()
        if suffix in (".gif", ".webp"):
            return candidate
    return image_candidates[0] if image_candidates else None


@dataclass(frozen=True)
class MemoryMediaSource:
    """In-memory ``MediaSource`` backed by downloaded bytes."""

    filename: NonEmptyStr
    content_type: str | None
    size: int
    data: bytes

    async def read(self) -> bytes:
        return self.data


class HttpRemoteMediaFetcher:
    """SSRF-safe HTTP resolver for direct media URLs and OpenGraph GIF pages."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
        max_html_bytes: int = MAX_HTML_BYTES,
    ) -> None:
        self._timeout = ClientTimeout(total=timeout_seconds)
        self._max_redirects = max_redirects
        self._max_html_bytes = max_html_bytes

    async def validate_url(self, url: str) -> None:
        """Ensure ``url`` uses HTTP(S) and resolves only to globally routable IPs."""
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError("Media URL must use HTTP or HTTPS.")
        host = parsed.hostname
        if not host:
            raise ValueError("Media URL has no host.")
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("Media URL has an invalid port.") from exc

        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise RuntimeError(f"Could not resolve media host {host}.") from exc
        if not infos:
            raise RuntimeError(f"Could not resolve media host {host}.")
        for info in infos:
            ip_str = str(info[4][0])
            if not _is_public_ip(ip_str):
                raise ValueError("Media URL resolves to a private or reserved address.")

    @staticmethod
    def _clean_mime(raw_content_type: str | None) -> str | None:
        if not raw_content_type:
            return None
        return raw_content_type.split(";", 1)[0].strip().lower() or None

    @staticmethod
    def _derive_filename(
        url: str,
        mime: str | None,
        fallback_stem: str | None = None,
    ) -> NonEmptyStr:
        path = PurePath(unquote(urlsplit(url).path))
        stem = path.stem.strip()
        if (not stem or stem.lower() in GENERIC_STEMS) and fallback_stem:
            stem = fallback_stem
        if not stem:
            stem = "media"
        ext = path.suffix.lower()
        if ext not in CAPTION_MEDIA_EXTENSIONS:
            ext = (
                MIME_TO_EXTENSION.get(mime or "")
                or (mimetypes.guess_extension(mime) if mime else None)
                or ".gif"
            )
            if ext == ".jpe":
                ext = ".jpg"
        return f"{stem}{ext}"

    @staticmethod
    async def _read_bounded(
        response: ClientResponse,
        max_bytes: int | None,
    ) -> bytes:
        if (
            max_bytes is not None
            and response.content_length is not None
            and response.content_length > max_bytes
        ):
            raise InputTooLargeError(response.content_length, max_bytes)
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise InputTooLargeError(total, max_bytes)
            chunks.append(chunk)
        return b"".join(chunks)

    async def _fetch_once(
        self,
        session: ClientSession,
        url: str,
        *,
        max_bytes: int | None,
        allow_html: bool,
        fallback_stem: str | None,
    ) -> MemoryMediaSource:
        current_url = url
        for _ in range(self._max_redirects + 1):
            await self.validate_url(current_url)
            try:
                async with session.get(
                    current_url,
                    allow_redirects=False,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                ) as response:
                    if response.status in REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            raise RuntimeError("Media redirect missing Location.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status >= 400:
                        raise RuntimeError(
                            f"Failed to fetch media URL (HTTP {response.status})."
                        )

                    mime = self._clean_mime(response.headers.get("Content-Type"))
                    url_path = unquote(urlsplit(current_url).path)
                    suffix = PurePath(url_path).suffix.lower()
                    is_media = (
                        mime is not None and mime.startswith(MEDIA_PREFIXES)
                    ) or suffix in CAPTION_MEDIA_EXTENSIONS
                    if is_media:
                        data = await self._read_bounded(response, max_bytes)
                        if not data:
                            raise ValueError("Remote media URL returned an empty file.")
                        filename = self._derive_filename(
                            current_url,
                            mime,
                            fallback_stem=fallback_stem,
                        )
                        return MemoryMediaSource(
                            filename=filename,
                            content_type=mime,
                            size=len(data),
                            data=data,
                        )

                    if allow_html and mime in ("text/html", "application/xhtml+xml"):
                        html_bytes = await self._read_bounded(
                            response,
                            self._max_html_bytes,
                        )
                        html = html_bytes.decode("utf-8", errors="replace")
                        media_url = extract_media_url_from_html(html, current_url)
                        if media_url is None:
                            raise ValueError(
                                "Could not find a supported image, GIF, or video at "
                                "that link."
                            )
                        page_stem = (
                            PurePath(unquote(urlsplit(current_url).path)).stem.strip()
                            or fallback_stem
                        )
                        return await self._fetch_once(
                            session,
                            media_url,
                            max_bytes=max_bytes,
                            allow_html=False,
                            fallback_stem=page_stem,
                        )

                    raise ValueError(
                        "That link does not point to a supported image, GIF, or video."
                    )
            except (ClientError, TimeoutError) as exc:
                raise RuntimeError(f"Failed to fetch media URL: {exc}") from exc

        raise RuntimeError("Media URL exceeded maximum redirect depth.")

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
    ) -> MemoryMediaSource:
        """Resolve and download ``url`` into an in-memory ``MediaSource``."""
        async with ClientSession(timeout=self._timeout) as session:
            return await self._fetch_once(
                session,
                url,
                max_bytes=max_bytes,
                allow_html=True,
                fallback_stem=None,
            )

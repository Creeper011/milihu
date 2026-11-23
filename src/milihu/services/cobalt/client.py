"""Cobalt HTTP adapter for media resolution."""

from typing import Any, Self, Unpack

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from milihu.exceptions.media import MediaBackendError
from milihu.types.download.format import DownloadFormat
from milihu.types.media import (
    MediaError,
    MediaProcessing,
    MediaResolveOptions,
    MediaResult,
    MediaSelection,
)


class Cobalt:
    """Share one HTTP session and translate Cobalt responses to media types."""

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Api-Key {self.api_key}"
        self.session = ClientSession(
            base_url=self.base_url,
            headers=headers,
            timeout=ClientTimeout(total=30),
        )
        try:
            async with self.session.get("/") as response:
                response.raise_for_status()
                info = await response.json()
                if "cobalt" not in info:
                    raise RuntimeError("The configured Cobalt instance is unavailable")
        except BaseException:
            await self.session.close()
            self.session = None
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        """Resolve a URL into data for orchestration, without fetching media."""
        if self.session is None:
            raise RuntimeError("Media backend is not connected")
        body = {"url": url}
        if "mode" in options:
            body["downloadMode"] = options["mode"]
        if "video_quality" in options:
            body["videoQuality"] = options["video_quality"]
        fmt = options.get("format")
        if fmt is not None:
            if fmt in (DownloadFormat.MP4, DownloadFormat.WEBM, DownloadFormat.MKV):
                body["youtubeVideoContainer"] = fmt
                if fmt == DownloadFormat.WEBM:
                    body["youtubeVideoCodec"] = "vp9"
            else:
                body["downloadMode"] = "audio"
                body["audioFormat"] = fmt
        try:
            async with self.session.post("/", json=body) as response:
                try:
                    data: dict[str, Any] = await response.json()
                except (ClientError, ValueError):
                    response.raise_for_status()
                    raise
                if data.get("status") != "error":
                    response.raise_for_status()
        except ClientResponseError as exc:
            raise MediaBackendError(str(exc), status=exc.status) from exc
        except (ClientError, TimeoutError) as exc:
            raise MediaBackendError(str(exc)) from exc

        match data.get("status"):
            case "tunnel" | "redirect" as status:
                return {
                    "status": "file",
                    "url": data["url"],
                    "filename": data["filename"],
                    "delivery": "proxy" if status == "tunnel" else "direct",
                }
            case "picker":
                selection: MediaSelection = {
                    "status": "selection",
                    "items": data["picker"],
                }
                if "audio" in data:
                    selection["audio"] = data["audio"]
                if "audioFilename" in data:
                    selection["audio_filename"] = data["audioFilename"]
                return selection
            case "local-processing":
                processing: MediaProcessing = {
                    "status": "processing",
                    "operation": data["type"],
                    "source": data["service"],
                    "urls": data["tunnel"],
                    "output": data["output"],
                }
                if "audio" in data:
                    processing["audio"] = data["audio"]
                if "isHLS" in data:
                    processing["is_hls"] = data["isHLS"]
                return processing
            case "error":
                error: MediaError = {"status": "error", "code": data["error"]["code"]}
                if "context" in data["error"]:
                    error["context"] = data["error"]["context"]
                return error
            case _:
                raise RuntimeError("Unsupported media backend response")

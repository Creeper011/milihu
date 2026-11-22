"""Resolve a media URL and fetch a file within Discord's upload limit."""

import logging
from pathlib import PurePath
from time import perf_counter

from aiohttp import ClientSession, ClientTimeout

from milihu.exceptions.payload import PayloadTooLargeError
from milihu.services.core.litterbox import Litterbox
from milihu.services.core.payload_fitter import PayloadFitter
from milihu.services.faces.media import MediaBackend
from milihu.types.default.upload import DEFAULT_MAX_UPLOAD_BYTES
from milihu.types.download.format import AUDIO_FORMATS
from milihu.types.download.output import DownloadOutput
from milihu.types.download.quality import QUALITY_LADDER, DownloadQuality
from milihu.types.download.request import DownloadRequest
from milihu.types.media import MediaFile


class DownloaderService:
    def __init__(
        self,
        backend: MediaBackend | None,
        uploader: Litterbox | None = None,
        payload_fitter: PayloadFitter | None = None,
        *,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.backend = backend
        self.uploader = uploader or Litterbox()
        self.payload_fitter = payload_fitter or PayloadFitter()
        self.max_upload_bytes = max_upload_bytes

    async def _resolve_file(
        self, request: DownloadRequest, quality: DownloadQuality
    ) -> tuple[MediaFile, PurePath]:
        assert self.backend is not None
        if request.format is None:
            result = await self.backend.resolve(
                str(request.url), video_quality=quality
            )
        else:
            result = await self.backend.resolve(
                str(request.url),
                format=request.format,
                video_quality=quality,
            )
        if result["status"] == "error":
            raise RuntimeError(f"Media backend rejected this URL: {result['code']}")
        if result["status"] != "file":
            raise RuntimeError("This URL requires media selection or processing.")

        path = PurePath(result["filename"])
        if request.format is not None and path.suffix.lower() != f".{request.format}":
            raise RuntimeError(
                f"This URL does not offer the {request.format.upper()} format."
            )
        return result, path

    @staticmethod
    def _raise_empty_response(
        result: MediaFile, status: int, content_length: int | None
    ) -> None:
        logging.getLogger(__name__).error(
            "Empty media response: delivery=%s status=%s content_length=%s",
            result["delivery"],
            status,
            content_length,
        )
        raise RuntimeError("Media source returned an empty file.")

    async def download(
        self, url: DownloadRequest | str, *, max_bytes: int
    ) -> DownloadOutput:
        if self.backend is None:
            raise RuntimeError("Media backend is not configured.")

        if isinstance(url, str):
            request = DownloadRequest.model_validate({"url": url})
        else:
            request = url
        started = perf_counter()
        if request.quality is not None:
            qualities = (request.quality,)
        elif request.format in AUDIO_FORMATS:
            qualities = (DownloadQuality.Q720,)
        else:
            qualities = QUALITY_LADDER
        limit_bytes = max(max_bytes, self.max_upload_bytes)
        first_result, first_path = await self._resolve_file(request, qualities[0])
        next_resolved: tuple[MediaFile, PurePath] | None = None
        step_down = False

        async def has_distinct_lower_quality() -> bool:
            nonlocal next_resolved
            if len(qualities) <= 1:
                return False
            if next_resolved is None:
                next_resolved = await self._resolve_file(request, qualities[1])
            return (next_resolved[0]["url"], next_resolved[0]["filename"]) != (
                first_result["url"],
                first_result["filename"],
            )

        async with ClientSession(timeout=ClientTimeout(total=120)) as session:
            data = bytearray()
            async with session.get(first_result["url"]) as response:
                response.raise_for_status()
                if (
                    response.content_length is not None
                    and response.content_length > max_bytes
                    and await has_distinct_lower_quality()
                ):
                    step_down = True
                elif (
                    response.content_length is not None
                    and response.content_length > limit_bytes
                ):
                    raise PayloadTooLargeError(
                        limit_bytes, size=response.content_length
                    )
                else:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        data.extend(chunk)
                        if (
                            len(data) > max_bytes
                            and next_resolved is None
                            and await has_distinct_lower_quality()
                        ):
                            step_down = True
                            break
                        if len(data) > limit_bytes:
                            raise PayloadTooLargeError(limit_bytes, size=len(data))
                    if not step_down and not data:
                        self._raise_empty_response(
                            first_result, response.status, response.content_length
                        )

            if not step_down:
                final_name = first_path.name
                payload = bytes(data)
            else:
                remaining = qualities[1:]
                assert remaining
                fitted_name = first_path.name
                fallback: list[tuple[str, bytes]] = []

                async def render(quality: DownloadQuality) -> bytes | None:
                    nonlocal next_resolved, fitted_name
                    if quality == remaining[0] and next_resolved is not None:
                        res, cand_path = next_resolved
                        next_resolved = None
                    else:
                        res, cand_path = await self._resolve_file(request, quality)
                    is_lowest = quality == remaining[-1]
                    budget = limit_bytes if is_lowest else max_bytes
                    async with session.get(res["url"]) as resp:
                        resp.raise_for_status()
                        if (
                            resp.content_length is not None
                            and resp.content_length > budget
                        ):
                            if is_lowest:
                                raise PayloadTooLargeError(
                                    limit_bytes, size=resp.content_length
                                )
                            return None
                        buf = bytearray()
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            buf.extend(chunk)
                            if len(buf) > budget:
                                if is_lowest:
                                    raise PayloadTooLargeError(
                                        limit_bytes, size=len(buf)
                                    )
                                return None
                        if not buf:
                            self._raise_empty_response(
                                res, resp.status, resp.content_length
                            )
                    raw = bytes(buf)
                    if len(raw) <= max_bytes:
                        fitted_name = cand_path.name
                        return raw
                    fallback.append((cand_path.name, raw))
                    return None

                try:
                    payload = await self.payload_fitter.fit(
                        remaining, render, max_bytes, prefer_first=True
                    )
                    final_name = fitted_name
                except PayloadTooLargeError:
                    if not fallback:
                        raise
                    final_name, payload = fallback[-1]

        uploaded_url = (
            await self.uploader.upload(payload, final_name)
            if len(payload) > max_bytes
            else None
        )
        return DownloadOutput(
            filename=final_name,
            data=payload,
            elapsed_seconds=perf_counter() - started,
            url=uploaded_url,
        )

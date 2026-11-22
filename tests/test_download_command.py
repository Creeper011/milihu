import re
import time
import unittest
from typing import Any, Self, cast

from discord import HTTPException, app_commands
from discord.ext import commands

from milihu.app.registry import build_cog_registry
from milihu.discord.cogs._base import BaseCog
from milihu.discord.cogs.download import DownloadCog
from milihu.exceptions.payload import PayloadTooLargeError
from milihu.faces.download import MediaDownloader
from milihu.types.default.payload import (
    BYTES_PER_MIB,
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_PAYLOAD_OVERHEAD_BYTES,
)
from milihu.types.download.format import DownloadFormat
from milihu.types.download.output import DownloadOutput
from milihu.types.download.quality import DownloadQuality
from milihu.types.download.request import DownloadRequest
from milihu.types.registry import BotClient
from milihu.usecases.downloader_service import DownloaderService
from milihu.usecases.faces.media import MediaBackend

_FAKE_BOT = cast(BotClient, object())


class Downloader:
    def __init__(
        self,
        filename: str = "video.mp4",
        ctx: "Context | None" = None,
        *,
        resolution: str | None = "1280x720",
        codec: str | None = "h264",
    ) -> None:
        self.filename = filename
        self.ctx = ctx
        self.resolution = resolution
        self.codec = codec
        self.downloaded_while_typing: bool | None = None
        self.request: DownloadRequest | None = None

    async def download(
        self, url: DownloadRequest | str, *, max_bytes: int
    ) -> DownloadOutput:
        assert isinstance(url, DownloadRequest)
        assert str(url.url) == "https://example.com/post"
        self.request = url
        if self.ctx is not None:
            self.downloaded_while_typing = self.ctx.in_typing
        return DownloadOutput(
            filename=self.filename,
            data=b"video",
            elapsed_seconds=1.25,
            resolution=self.resolution,
            codec=self.codec,
        )


class ReplyMessage:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.initial_kwargs = kwargs
        self.edit_kwargs: dict[str, Any] | None = None
        self.deleted = False

    async def edit(self, **kwargs: Any) -> Self:
        self.edit_kwargs = kwargs
        return self

    async def delete(self) -> None:
        self.deleted = True


class Context:
    guild: Any = None
    interaction: Any = None
    reply_kwargs: dict[str, Any] | None = None

    def __init__(self) -> None:
        self.in_typing = False
        self.replies: list[ReplyMessage] = []
        self.reply_typing_states: list[bool] = []

    def typing(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        self.in_typing = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.in_typing = False

    async def reply(self, content: str | None = None, **kwargs: Any) -> ReplyMessage:
        if content is not None:
            kwargs["content"] = content
        self.reply_kwargs = kwargs
        self.reply_typing_states.append(self.in_typing)
        message = ReplyMessage(kwargs)
        self.replies.append(message)
        return message


def _as_ctx(ctx: object) -> commands.Context[BotClient]:
    return cast(commands.Context[BotClient], ctx)


def _make_cog(downloader: MediaDownloader) -> DownloadCog:
    cog = DownloadCog(_FAKE_BOT, downloader)
    cog.download.cog = cog
    return cog


class DownloadCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_injects_backend_without_bot_state(self) -> None:
        backend = cast(MediaBackend, object())
        bot = cast(BotClient, object())
        cog = build_cog_registry(backend)[DownloadCog](bot)
        self.assertIsInstance(cog, DownloadCog)
        assert isinstance(cog, DownloadCog)
        self.assertIsInstance(cog.downloader, DownloaderService)
        assert isinstance(cog.downloader, DownloaderService)
        self.assertIs(cog.downloader.backend, backend)
        self.assertFalse(hasattr(bot, "media_backend"))

    async def test_download_replies_with_video_and_metadata(self) -> None:
        ctx = Context()
        downloader = Downloader(ctx=ctx)
        cog = _make_cog(downloader)
        await cog.download(_as_ctx(ctx), "https://example.com/post")
        self.assertTrue(downloader.downloaded_while_typing)
        self.assertEqual(ctx.reply_typing_states, [False])
        assert downloader.request is not None
        self.assertIsNone(downloader.request.format)
        self.assertIsNone(downloader.request.quality)

        [status_msg] = ctx.replies
        status_view = status_msg.initial_kwargs["view"]
        self.assertTrue(status_view.has_components_v2())
        [status_container] = status_view.to_components()
        self.assertEqual(status_container["type"], 17)
        [status_text] = status_container["components"]
        self.assertEqual(status_text["type"], 10)
        match = re.search(r"Sending.*<t:(\d+):R>", status_text["content"])
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreater(int(match.group(1)), int(time.time()))

        assert status_msg.edit_kwargs is not None
        [uploaded_file] = status_msg.edit_kwargs["attachments"]
        self.assertEqual(uploaded_file.filename, "video.mp4")
        self.assertEqual(uploaded_file.fp.read(), b"video")
        view = status_msg.edit_kwargs["view"]
        self.assertTrue(view.has_components_v2())
        [container] = view.to_components()
        self.assertEqual(container["type"], 17)
        gallery, separator, metadata = container["components"]
        self.assertEqual(gallery["type"], 12)
        self.assertEqual(
            gallery["items"][0]["media"]["url"],
            "attachment://video.mp4",
        )
        self.assertEqual(separator["type"], 14)
        self.assertTrue(separator["divider"])
        self.assertEqual(metadata["type"], 10)
        self.assertEqual(
            metadata["content"],
            "-# 1280x720 • h264 • 5 B • Downloaded in 1.25 s",
        )

    async def test_download_replies_with_temporary_url_when_file_exceeds_limit(
        self,
    ) -> None:
        class LargeDownloader:
            async def download(
                self, url: DownloadRequest | str, *, max_bytes: int
            ) -> DownloadOutput:
                return DownloadOutput(
                    filename="video.mp4",
                    data=b"x" * 1024,
                    elapsed_seconds=1.25,
                    url="https://litter.catbox.moe/abc123.mp4",
                    resolution="1920x1080",
                    codec="av1",
                )

        ctx = Context()
        cog = _make_cog(LargeDownloader())
        await cog.download(_as_ctx(ctx), "https://example.com/post")

        [status_msg] = ctx.replies
        assert status_msg.edit_kwargs is not None
        self.assertNotIn("attachments", status_msg.edit_kwargs)
        [container] = status_msg.edit_kwargs["view"].to_components()
        notice, link, separator, metadata = container["components"]
        self.assertEqual(notice["type"], 10)
        self.assertTrue(
            notice["content"].startswith("### Oops, Discord couldn't handle it")
        )
        self.assertIn("temporary file service", notice["content"])
        self.assertIn("The duration of this link is 12h.", notice["content"])
        self.assertEqual(link["type"], 10)
        self.assertEqual(link["content"], "https://litter.catbox.moe/abc123.mp4")
        self.assertEqual(separator["type"], 14)
        self.assertTrue(separator["divider"])
        self.assertEqual(
            metadata["content"],
            "-# 1920x1080 • av1 • 1.00 KiB • Downloaded in 1.25 s",
        )

    async def test_download_replies_with_native_audio_attachment_and_metadata(
        self,
    ) -> None:
        ctx = Context()
        cog = _make_cog(Downloader("audio.mp3", resolution=None, codec="mp3"))
        await cog.download(_as_ctx(ctx), "https://example.com/post")
        status_msg, audio_msg = ctx.replies
        self.assertTrue(status_msg.deleted)
        self.assertNotIn("view", audio_msg.initial_kwargs)
        self.assertEqual(audio_msg.initial_kwargs["file"].filename, "audio.mp3")
        self.assertEqual(
            audio_msg.initial_kwargs["content"],
            "-# mp3 • 5 B • Downloaded in 1.25 s",
        )

    async def test_download_passes_a_validated_format_to_the_service(self) -> None:
        ctx = Context()
        downloader = Downloader("audio.wav")
        cog = _make_cog(downloader)
        await cog.download(_as_ctx(ctx), "https://example.com/post", "wav", "1080")

        self.assertIsInstance(downloader.request, DownloadRequest)
        assert downloader.request is not None
        self.assertIs(downloader.request.format, DownloadFormat.WAV)
        self.assertIs(downloader.request.quality, DownloadQuality.Q1080)
        assert ctx.reply_kwargs is not None
        self.assertEqual(ctx.reply_kwargs["file"].filename, "audio.wav")

    async def test_download_registers_optional_format_choices(self) -> None:
        app_command = DownloadCog.download.app_command
        assert app_command is not None
        _, format_option, quality_option = app_command.parameters
        self.assertFalse(format_option.required)
        self.assertEqual(
            {choice.value for choice in format_option.choices},
            {"mp3", "ogg", "wav", "opus", "mp4", "webm", "mkv"},
        )
        self.assertFalse(quality_option.required)
        self.assertIsNone(quality_option.default)
        self.assertEqual(
            {(choice.name, choice.value) for choice in quality_option.choices},
            {
                ("1440p", "1440"),
                ("1080p", "1080"),
                ("720p", "720"),
                ("480p", "480"),
                ("360p", "360"),
                ("240p", "240"),
                ("144p", "144"),
            },
        )

    async def test_download_rejects_invalid_requests_before_downloading(self) -> None:
        for url, format, quality in (
            ("ftp://example.com/post", None, "720"),
            ("https://example.com/post", "zip", "720"),
            ("https://example.com/post", None, "999"),
        ):
            with self.subTest(url=url, format=format, quality=quality):
                ctx = Context()
                cog = _make_cog(Downloader())
                await cog.download(_as_ctx(ctx), url, format, quality)

                assert ctx.reply_kwargs is not None
                self.assertTrue(ctx.reply_kwargs["ephemeral"])
                self.assertIn("HTTP", ctx.reply_kwargs["content"])
                self.assertIn("mp3", ctx.reply_kwargs["content"])
                self.assertIn("720", ctx.reply_kwargs["content"])

    async def test_slash_download_inherits_payload_error_reply(self) -> None:
        cog = DownloadCog(_FAKE_BOT, Downloader())
        app_command = cog.download.app_command
        assert app_command is not None
        response: Any = type("Response", (), {"status": 413, "reason": "Too Large"})()
        for original in (
            PayloadTooLargeError(8 * 1024**2, size=10 * 1024**2),
            HTTPException(response, {"code": 40005, "message": "Too Large"}),
        ):
            with self.subTest(error=type(original).__name__):
                ctx = Context()
                error = commands.HybridCommandError(
                    app_commands.CommandInvokeError(app_command, original)
                )
                await cog.cog_command_error(_as_ctx(ctx), error)

                assert ctx.reply_kwargs is not None
                self.assertNotIn("file", ctx.reply_kwargs)
                [container] = ctx.reply_kwargs["view"].to_components()
                title, _ = container["components"]
                self.assertEqual(title["content"], "### File Too Large")

    def test_get_upload_limit_scales_with_guild_and_interaction(self) -> None:
        ctx = Context()
        self.assertEqual(
            BaseCog.get_upload_limit(_as_ctx(ctx)),
            DEFAULT_FILE_SIZE_LIMIT_BYTES - DEFAULT_PAYLOAD_OVERHEAD_BYTES,
        )

        ctx.guild = type("Guild", (), {"filesize_limit": 10 * BYTES_PER_MIB})()
        self.assertEqual(
            BaseCog.get_upload_limit(_as_ctx(ctx)),
            DEFAULT_FILE_SIZE_LIMIT_BYTES - DEFAULT_PAYLOAD_OVERHEAD_BYTES,
        )

        ctx.guild = type("Guild", (), {"filesize_limit": 50 * BYTES_PER_MIB})()
        self.assertEqual(
            BaseCog.get_upload_limit(_as_ctx(ctx)),
            50 * BYTES_PER_MIB - DEFAULT_PAYLOAD_OVERHEAD_BYTES,
        )

        ctx.interaction = type(
            "Interaction", (), {"filesize_limit": 500 * BYTES_PER_MIB}
        )()
        self.assertEqual(
            BaseCog.get_upload_limit(_as_ctx(ctx)),
            500 * BYTES_PER_MIB - DEFAULT_PAYLOAD_OVERHEAD_BYTES,
        )

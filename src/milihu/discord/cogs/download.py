"""Discord command for downloading and displaying media from a URL."""

from datetime import timedelta
from mimetypes import guess_file_type
from typing import override

from discord import File, app_commands, utils
from discord.ext import commands
from discord.ui import Container, LayoutView, MediaGallery, Separator, TextDisplay
from discord.ui import File as FileComponent
from pydantic import ValidationError

from milihu.discord.cogs._base import BaseCog
from milihu.discord.types.bot import BotClient
from milihu.exceptions.download import DownloadError
from milihu.faces.download import MediaDownloader
from milihu.types.default.payload import BYTES_PER_MIB
from milihu.types.default.upload import DEFAULT_UPLOAD_LIFETIME
from milihu.types.download.format import FORMAT_ALIASES, DownloadFormat
from milihu.types.download.output import DownloadOutput
from milihu.types.download.quality import DownloadQuality
from milihu.types.download.request import DownloadRequest
from milihu.types.media import MEDIA_PREFIXES


class DownloadCog(BaseCog):
    def __init__(self, bot: BotClient, downloader: MediaDownloader) -> None:
        super().__init__(bot)
        self.downloader = downloader

    @staticmethod
    def _format_metadata(output: DownloadOutput) -> str:
        if output.size_bytes >= BYTES_PER_MIB:
            size = f"{output.size_bytes / BYTES_PER_MIB:.2f} MiB"
        elif output.size_bytes >= 1024:
            size = f"{output.size_bytes / 1024:.2f} KiB"
        else:
            size = f"{output.size_bytes} B"

        parts = [
            part
            for part in (
                output.resolution,
                output.codec,
                size,
                f"Downloaded in {output.elapsed_seconds:.2f} s",
            )
            if part
        ]
        return f"-# {' • '.join(parts)}"

    @staticmethod
    def _build_sending_view(output: DownloadOutput) -> LayoutView:
        eta_seconds = max(
            5.0,
            output.elapsed_seconds,
            output.size_bytes / (2 * BYTES_PER_MIB),
        )
        timestamp = utils.format_dt(
            utils.utcnow() + timedelta(seconds=eta_seconds), "R"
        )
        return LayoutView().add_item(
            Container[LayoutView](
                TextDisplay[LayoutView](f"Sending media... • {timestamp}")
            )
        )

    @classmethod
    def _build_view(cls, file: File | None, output: DownloadOutput) -> LayoutView:
        metadata = TextDisplay[LayoutView](cls._format_metadata(output))
        if file is None:
            notice = TextDisplay[LayoutView](
                "### Oops, Discord couldn't handle it, the file was sooo big, "
                "so we sent it to a temporary file service. "
                f"The duration of this link is {DEFAULT_UPLOAD_LIFETIME}."
            )
            return LayoutView().add_item(
                Container[LayoutView](
                    notice,
                    TextDisplay[LayoutView](output.url or ""),
                    Separator[LayoutView](),
                    metadata,
                )
            )

        content_type, _ = guess_file_type(file.filename)
        if content_type and content_type.startswith(MEDIA_PREFIXES):
            media: MediaGallery[LayoutView] | FileComponent[LayoutView] = (
                MediaGallery[LayoutView]().add_item(media=file)
            )
        else:
            media = FileComponent[LayoutView](media=file)
        return LayoutView().add_item(
            Container[LayoutView](media, Separator[LayoutView](), metadata)
        )

    @commands.hybrid_command(name="download", aliases=["d"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.user)
    @app_commands.describe(
        format="Output format. Leave blank for automatic selection.",
        quality="Video resolution. Leave blank for automatic selection.",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name=fmt.value.upper(), value=fmt.value)
            for fmt in DownloadFormat
        ],
        quality=[
            app_commands.Choice(name=f"{q.value}p", value=q.value)
            for q in DownloadQuality
        ],
    )
    async def download(
        self,
        ctx: commands.Context[BotClient],
        url: str | None = None,
        format: str | None = None,
        quality: str | None = None,
    ) -> None:
        """Yeaaa, download "anything" from internet!"""
        if ctx.interaction is None:
            if url is not None and url in FORMAT_ALIASES:
                selected = FORMAT_ALIASES[url]
                url, format = format, selected
            elif format is not None and format in FORMAT_ALIASES:
                format = FORMAT_ALIASES[format]
            if url is not None:
                url = self._extract_content_url(url) or url
            url = url or self.extract_reply_url(ctx)

        if url is None:
            await ctx.reply(
                "Provide a URL or reply to a message with a link. "
                "Example: m$download video",
                ephemeral=True,
            )
            return
        try:
            request = DownloadRequest.model_validate(
                {"url": url, "format": format, "quality": quality}
            )
        except ValidationError:
            formats = ", ".join(DownloadFormat)
            qualities = ", ".join(DownloadQuality)
            await ctx.reply(
                f"Use a valid HTTP(S) URL. Supported formats are {formats}. "
                f"Supported qualities are {qualities}.",
                ephemeral=True,
            )
            return

        async with ctx.typing():
            try:
                output = await self.downloader.download(
                    request, max_bytes=self.get_upload_limit(ctx)
                )
            except (DownloadError, RuntimeError, TimeoutError) as exc:
                await ctx.reply(str(exc), ephemeral=True)
                return

        status_message = await ctx.reply(view=self._build_sending_view(output))
        if output.url is not None:
            await status_message.edit(view=self._build_view(None, output))
            return
        content_type, _ = guess_file_type(output.filename)
        if content_type and content_type.startswith("audio/"):
            await self.reply_file(
                ctx,
                output.data,
                filename=output.filename,
                content=self._format_metadata(output),
                message=status_message,
            )
            return
        await self.reply_file(
            ctx,
            output.data,
            filename=output.filename,
            view_factory=lambda file: self._build_view(file, output),
            message=status_message,
        )

    # The prefix accepts a missing URL to read a replied message; slash requires one.
    assert download.app_command is not None
    download.app_command._params["url"].required = True  # pyright: ignore[reportPrivateUsage]

    @override
    async def cog_command_error(
        self, ctx: commands.Context[BotClient], error: Exception
    ) -> None:
        original = self._unwrap_command_error(error)
        if isinstance(original, commands.CommandOnCooldown):
            await ctx.reply(
                f"Try downloading again in {original.retry_after:.0f} seconds.",
                ephemeral=True,
            )
            return
        if isinstance(original, commands.MaxConcurrencyReached):
            await ctx.reply("Your download is already running.", ephemeral=True)
            return
        await super().cog_command_error(ctx, error)

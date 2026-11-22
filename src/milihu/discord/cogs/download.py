"""Discord command for downloading and displaying media from a URL."""

from datetime import timedelta
from mimetypes import guess_file_type

from discord import File, app_commands, utils
from discord.ext import commands
from discord.ui import Container, LayoutView, MediaGallery, Separator, TextDisplay
from discord.ui import File as FileComponent
from pydantic import ValidationError

from milihu.discord.cogs._base import BaseCog
from milihu.exceptions.download import DownloadError
from milihu.faces.download import MediaDownloader
from milihu.types.default.payload import BYTES_PER_MIB
from milihu.types.default.upload import DEFAULT_UPLOAD_LIFETIME
from milihu.types.download.format import DownloadFormat
from milihu.types.download.output import DownloadOutput
from milihu.types.download.quality import DownloadQuality
from milihu.types.download.request import DownloadRequest
from milihu.types.media import MEDIA_PREFIXES
from milihu.types.registry import BotClient


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

    @commands.hybrid_command(name="download")
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
        url: str,
        format: str | None = None,
        quality: str | None = None,
    ) -> None:
        """Yeaaa, download "anything" from internet!"""
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

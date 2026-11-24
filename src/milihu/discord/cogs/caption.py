"""Cog for adding text captions to images, GIFs, and videos."""

import io
from typing import override

from discord import (
    Attachment,
    Client,
    File,
    HTTPException,
    Interaction,
    Message,
    SelectOption,
    TextStyle,
    app_commands,
)
from discord.ext import commands
from discord.ui import (
    Container,
    Label,
    LayoutView,
    MediaGallery,
    Modal,
    Select,
    TextDisplay,
    TextInput,
)

from milihu.discord.cogs._base import BaseCog
from milihu.discord.types.bot import BotClient
from milihu.exceptions.remote_media import RemoteMediaError
from milihu.exceptions.payload import PayloadTooLargeError
from milihu.faces.caption import MediaCaptioner
from milihu.faces.media import MediaSource
from milihu.types.caption import (
    CAPTION_POSITIONS,
    CAPTION_STYLES,
    CaptionPosition,
    CaptionStyle,
)
from milihu.types.default.caption import (
    DEFAULT_CAPTION_MAX_TEXT_LENGTH,
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
)
from milihu.types.default.payload import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_PAYLOAD_OVERHEAD_BYTES,
)


class _CaptionModal(Modal, title="Add Caption"):
    def __init__(self, cog: "CaptionCog", target: Attachment | str) -> None:
        super().__init__()
        self.cog = cog
        self.target = target

        self.caption_input: TextInput[Modal] = TextInput(
            label="Caption",
            style=TextStyle.short,
            required=True,
            max_length=DEFAULT_CAPTION_MAX_TEXT_LENGTH,
            placeholder="Write your caption...",
        )
        self.position_select: Select[Modal] = Select(
            options=[
                SelectOption(
                    label=pos.capitalize(),
                    value=pos,
                    default=pos == DEFAULT_CAPTION_POSITION,
                )
                for pos in CAPTION_POSITIONS
            ]
        )
        self.style_select: Select[Modal] = Select(
            options=[
                SelectOption(
                    label=style.capitalize(),
                    value=style,
                    default=style == DEFAULT_CAPTION_STYLE,
                )
                for style in CAPTION_STYLES
            ]
        )

        self.add_item(self.caption_input)
        self.add_item(Label(text="Position", component=self.position_select))
        self.add_item(Label(text="Style", component=self.style_select))

    @override
    async def on_submit(self, interaction: Interaction[Client]) -> None:
        raw_position = (
            self.position_select.values[0]
            if self.position_select.values
            else DEFAULT_CAPTION_POSITION
        )
        raw_style = (
            self.style_select.values[0]
            if self.style_select.values
            else DEFAULT_CAPTION_STYLE
        )
        position: CaptionPosition = (
            raw_position
            if raw_position in CAPTION_POSITIONS
            else DEFAULT_CAPTION_POSITION
        )
        style: CaptionStyle = (
            raw_style
            if raw_style in CAPTION_STYLES
            else DEFAULT_CAPTION_STYLE
        )
        await self.cog.handle_modal_submit(
            interaction,
            self.target,
            self.caption_input.value,
            position=position,
            style=style,
        )


class CaptionCog(BaseCog):
    """Presents the caption command and message context menu."""

    def __init__(self, bot: BotClient, captioner: MediaCaptioner) -> None:
        super().__init__(bot)
        self.captioner = captioner
        self.ctx_menu = app_commands.ContextMenu(
            name="Caption",
            callback=self.open_caption_modal,
        )

    @override
    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.ctx_menu)

    @override
    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @staticmethod
    def _build_view(file: File, output_name: str) -> LayoutView:
        gallery = MediaGallery[LayoutView]()
        gallery.add_item(media=file)
        view = LayoutView()
        view.add_item(
            Container[LayoutView](
                TextDisplay[LayoutView]("### Captioned Media"),
                gallery,
                TextDisplay[LayoutView](f"-# `{output_name}`"),
            )
        )
        return view

    @staticmethod
    def _get_interaction_upload_limit(
        interaction: Interaction[Client],
        *,
        overhead_bytes: int = DEFAULT_PAYLOAD_OVERHEAD_BYTES,
    ) -> int:
        limit = max(
            DEFAULT_FILE_SIZE_LIMIT_BYTES,
            interaction.guild.filesize_limit if interaction.guild is not None else 0,
            interaction.filesize_limit,
        )
        return max(1, limit - overhead_bytes)

    def _is_valid_target(self, target: Attachment | str | None) -> bool:
        if target is None:
            return False
        if isinstance(target, str):
            return True
        return self.captioner.is_supported(target)

    async def open_caption_modal(
        self,
        interaction: Interaction[BotClient],
        message: Message,
    ) -> None:
        """Open the caption modal for a message selected via the context menu."""
        target = self.extract_media_from_message(message)
        if not self._is_valid_target(target) or target is None:
            await interaction.response.send_message(
                "Please select a message with a valid image, GIF, video, or link.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(_CaptionModal(self, target))

    async def handle_modal_submit(
        self,
        interaction: Interaction[Client],
        target: MediaSource | str,
        text: str,
        *,
        position: CaptionPosition = DEFAULT_CAPTION_POSITION,
        style: CaptionStyle = DEFAULT_CAPTION_STYLE,
    ) -> None:
        """Process a submitted caption modal and send the captioned media."""
        await interaction.response.defer(thinking=True)
        max_bytes = self._get_interaction_upload_limit(interaction)
        try:
            output = await self.captioner.caption(
                target,
                text,
                position=position,
                style=style,
                max_bytes=max_bytes,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except PayloadTooLargeError as exc:
            await interaction.followup.send(
                view=self.formatter.error_view("File Too Large", str(exc))
            )
            return
        except RemoteMediaError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except RuntimeError:
            await interaction.followup.send("Failed to caption the file.")
            return

        if len(output.data) > max_bytes:
            error = PayloadTooLargeError(max_bytes, size=len(output.data))
            await interaction.followup.send(
                view=self.formatter.error_view("File Too Large", str(error))
            )
            return

        file = File(io.BytesIO(output.data), filename=output.filename)
        try:
            await interaction.followup.send(
                file=file,
                view=self._build_view(file, output.filename),
            )
        except HTTPException as exc:
            if self.is_payload_too_large(exc):
                await interaction.followup.send(
                    view=self.formatter.error_view(
                        "File Too Large",
                        str(PayloadTooLargeError()),
                    )
                )
                return
            raise

    @commands.command(name="caption", aliases=["cap", "c"])
    async def caption(
        self,
        ctx: commands.Context[BotClient],
        *,
        text: str,
    ) -> None:
        """Caption an attached or replied-to image, GIF, or video."""
        target = self.extract_media(ctx)
        if not self._is_valid_target(target) or target is None:
            await ctx.reply(
                "Please attach or reply to a valid image, GIF, video, or link.",
                ephemeral=True,
            )
            return

        max_bytes = self.get_upload_limit(ctx)

        async with ctx.typing():
            try:
                output = await self.captioner.caption(
                    target,
                    text,
                    position=DEFAULT_CAPTION_POSITION,
                    style=DEFAULT_CAPTION_STYLE,
                    max_bytes=max_bytes,
                )
            except ValueError as exc:
                await ctx.reply(str(exc), ephemeral=True)
                return
            except RemoteMediaError as exc:
                await ctx.reply(str(exc), ephemeral=True)
                return
            except RuntimeError:
                await ctx.reply("Failed to caption the file.")
                return

            await self.reply_file(
                ctx,
                output.data,
                filename=output.filename,
                view_factory=lambda file: self._build_view(file, output.filename),
            )

    @app_commands.command(name="caption")
    @app_commands.describe(
        text="Text to render as a caption.",
        attachment="Image, GIF, or video file to caption.",
        url="URL of the image, GIF, or video to caption.",
        position="Caption placement (top or bottom). Defaults to top.",
        style="Caption style (bar or overlay). Defaults to bar.",
    )
    async def caption_slash(
        self,
        interaction: Interaction[BotClient],
        text: str,
        attachment: Attachment | None = None,
        url: str | None = None,
        position: CaptionPosition | None = None,
        style: CaptionStyle | None = None,
    ) -> None:
        """Add a caption to an attachment or URL."""
        if (attachment is None) == (url is None):
            await interaction.response.send_message(
                "Provide exactly one source: an attachment or a URL.",
                ephemeral=True,
            )
            return
        target = (
            attachment
            if attachment is not None
            else self._extract_content_url(url or "") or url
        )
        if not self._is_valid_target(target) or target is None:
            await interaction.response.send_message(
                "Please provide a valid image, GIF, video, or link.",
                ephemeral=True,
            )
            return
        await self.handle_modal_submit(
            interaction,
            target,
            text,
            position=position or DEFAULT_CAPTION_POSITION,
            style=style or DEFAULT_CAPTION_STYLE,
        )

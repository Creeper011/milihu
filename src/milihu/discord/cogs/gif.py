"""Cog for converting images and videos into GIFs."""

from pathlib import Path
from typing import override

from discord import File, Interaction, Message, app_commands
from discord.ext import commands
from discord.ui import Container, LayoutView, MediaGallery, TextDisplay

from milihu.discord.cogs._base import BaseCog
from milihu.faces.togif import GifConverter
from milihu.types.registry import BotClient


class GifCog(BaseCog):
    """Presents the GIF conversion command."""

    def __init__(self, bot: BotClient, converter: GifConverter) -> None:
        super().__init__(bot)
        self.converter = converter
        self.ctx_menu = app_commands.ContextMenu(
            name="To GIF",
            callback=self._context_to_gif,
        )

    @override
    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.ctx_menu)

    @override
    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def _context_to_gif(
        self,
        interaction: Interaction[BotClient],
        message: Message,
    ) -> None:
        interaction.message = message
        ctx = await self.bot.get_context(interaction)
        try:
            await self.to_gif(ctx)
        except Exception as exc:
            await self.cog_command_error(ctx, exc)

    @staticmethod
    def _build_view(file: File, output_name: str) -> LayoutView:
        gallery = MediaGallery[LayoutView]()
        gallery.add_item(media=file)
        view = LayoutView()
        view.add_item(
            Container[LayoutView](
                TextDisplay[LayoutView]("### Generated GIF"),
                gallery,
                TextDisplay[LayoutView](f"-# `{output_name}`"),
            )
        )
        return view

    @commands.hybrid_command(name="gif")
    async def to_gif(self, ctx: commands.Context[BotClient]) -> None:
        """Convert an attached or replied-to image/video to a GIF."""
        attachment = self.extract_attachment(ctx)
        if attachment is None or not self.converter.is_supported(attachment):
            await ctx.reply(
                "Please attach or reply to a valid image or video.",
                ephemeral=True,
            )
            return

        max_bytes = self.get_upload_limit(ctx)
        async with ctx.typing():
            try:
                gif_bytes = await self.converter.convert(
                    attachment,
                    max_bytes=max_bytes,
                )
            except RuntimeError:
                await ctx.reply("Failed to convert the file to GIF.")
                return

            output_name = f"{Path(attachment.filename).stem}.gif"
            await self.reply_file(
                ctx,
                gif_bytes,
                filename=output_name,
                view_factory=lambda file: self._build_view(file, output_name),
            )

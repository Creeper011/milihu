"""Shared base cog for attachment handling, size-safe replies, and payload errors."""

import io
from collections.abc import Callable
from typing import Any, override

from discord import Attachment, Embed, File, HTTPException, Message, app_commands
from discord.ext import commands
from discord.ui import LayoutView

from milihu.discord.cogs._formatter import CogMessageFormatter
from milihu.discord.types.bot import BotClient
from milihu.exceptions.payload import PayloadTooLargeError
from milihu.types.default.cogs import (
    URL_RE,
    URL_TRAILING_PUNCT,
)
from milihu.types.default.payload import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_PAYLOAD_OVERHEAD_BYTES,
)


class BaseCog(commands.Cog):
    """Base cog providing common Discord attachment and payload-limit handling."""

    def __init__(
        self,
        bot: BotClient,
        formatter: CogMessageFormatter | None = None,
    ) -> None:
        self.bot = bot
        self.formatter = formatter or CogMessageFormatter()

    @staticmethod
    def _resolved_reference(message: Message) -> Message | None:
        ref = message.reference
        if ref is None:
            return None
        if isinstance(ref.resolved, Message):
            return ref.resolved
        if isinstance(ref.cached_message, Message):
            return ref.cached_message
        return None

    @staticmethod
    def _extract_embed_media_url(embeds: list[Embed]) -> str | None:
        for embed in embeds:
            for candidate in (
                embed.video.url,
                embed.video.proxy_url,
                embed.image.url,
                embed.image.proxy_url,
                embed.thumbnail.url,
                embed.thumbnail.proxy_url,
                embed.url,
            ):
                if candidate:
                    return candidate
        return None

    @staticmethod
    def _extract_content_url(content: str) -> str | None:
        match = URL_RE.search(content)
        if match is None:
            return None
        return match.group(0).rstrip(URL_TRAILING_PUNCT) or None

    @staticmethod
    def extract_attachment(
        ctx: commands.Context[BotClient],
        *,
        index: int = 0,
    ) -> Attachment | None:
        """Return attachment ``index`` from the command or replied-to message."""
        if len(ctx.message.attachments) > index:
            return ctx.message.attachments[index]
        replied = BaseCog._resolved_reference(ctx.message)
        if replied is not None and len(replied.attachments) > index:
            return replied.attachments[index]
        return None

    @classmethod
    def extract_media_from_message(
        cls,
        message: Message,
        *,
        index: int = 0,
        include_content_url: bool = True,
    ) -> Attachment | str | None:
        """Extract an attachment, embed media URL, or link URL from ``message``."""
        if len(message.attachments) > index:
            return message.attachments[index]
        embed_url = cls._extract_embed_media_url(message.embeds)
        if embed_url is not None:
            return embed_url
        if include_content_url:
            return cls._extract_content_url(message.content)
        return None

    @classmethod
    def extract_media(
        cls,
        ctx: commands.Context[BotClient],
        *,
        index: int = 0,
    ) -> Attachment | str | None:
        """Extract media (attachment, embed, or link) from command or reply."""
        attachment = cls.extract_attachment(ctx, index=index)
        if attachment is not None:
            return attachment

        replied = cls._resolved_reference(ctx.message)
        for msg in (ctx.message, replied):
            if msg is None:
                continue
            embed_url = cls._extract_embed_media_url(msg.embeds)
            if embed_url is not None:
                return embed_url

        if replied is not None:
            replied_url = cls._extract_content_url(replied.content)
            if replied_url is not None:
                return replied_url

        return cls._extract_content_url(ctx.message.content)

    @staticmethod
    def get_upload_limit(
        ctx: commands.Context[BotClient],
        *,
        overhead_bytes: int = DEFAULT_PAYLOAD_OVERHEAD_BYTES,
    ) -> int:
        """Return the safe maximum file size in bytes for replying in ``ctx``."""
        limit = max(
            DEFAULT_FILE_SIZE_LIMIT_BYTES,
            ctx.guild.filesize_limit if ctx.guild is not None else 0,
            ctx.interaction.filesize_limit if ctx.interaction is not None else 0,
        )
        return max(1, limit - overhead_bytes)

    @staticmethod
    def is_payload_too_large(exc: HTTPException) -> bool:
        """Return whether ``exc`` is Discord's 413 / 40005 payload-too-large error."""
        return exc.status == 413 or exc.code == 40005

    async def reply_file(
        self,
        ctx: commands.Context[BotClient],
        data: bytes,
        *,
        filename: str,
        view_factory: Callable[[File], LayoutView] | None = None,
        content: str | None = None,
        message: Message | None = None,
        overhead_bytes: int = DEFAULT_PAYLOAD_OVERHEAD_BYTES,
    ) -> Message | None:
        """Reply with a Discord file after checking the upload size limit."""
        max_bytes = self.get_upload_limit(ctx, overhead_bytes=overhead_bytes)
        if len(data) > max_bytes:
            error = PayloadTooLargeError(max_bytes, size=len(data))
            error_view = self.formatter.error_view("File Too Large", str(error))
            if message is not None:
                await message.edit(view=error_view)
            else:
                await ctx.reply(view=error_view)
            return None

        file = File(io.BytesIO(data), filename=filename)
        if message is not None and view_factory is not None:
            return await message.edit(
                attachments=[file],
                view=view_factory(file),
            )

        kwargs: dict[str, Any] = {"file": file}
        if view_factory is not None:
            kwargs["view"] = view_factory(file)
        if content is not None:
            kwargs["content"] = content

        sent = await ctx.reply(**kwargs)
        if message is not None:
            await message.delete()
        return sent

    @staticmethod
    def _unwrap_command_error(error: Exception) -> Exception:
        original = error
        while isinstance(
            original,
            (
                commands.CommandInvokeError,
                commands.HybridCommandError,
                app_commands.CommandInvokeError,
            ),
        ):
            original = original.original
        return original

    @override
    async def cog_command_error(
        self,
        ctx: commands.Context[BotClient],
        error: Exception,
    ) -> None:
        """Handle payload-too-large errors uniformly across all commands in the cog."""
        original = self._unwrap_command_error(error)
        if isinstance(original, PayloadTooLargeError):
            await ctx.reply(
                view=self.formatter.error_view("File Too Large", str(original))
            )
            return
        if isinstance(original, HTTPException) and self.is_payload_too_large(original):
            await ctx.reply(
                view=self.formatter.error_view(
                    "File Too Large",
                    str(PayloadTooLargeError()),
                )
            )
            return
        raise error

import unittest
from dataclasses import dataclass
from typing import Any, Self, cast
from unittest.mock import AsyncMock, MagicMock

from discord import (
    AppCommandOptionType,
    Attachment,
    Client,
    Embed,
    Interaction,
    Message,
)
from discord.ext import commands
from discord.ui import Container, LayoutView, MediaGallery, Modal, TextDisplay

from milihu.app.registry import build_cog_registry
from milihu.discord.cogs.caption import CaptionCog
from milihu.discord.cogs.gif import GifCog
from milihu.exceptions.payload import PayloadTooLargeError
from milihu.faces.caption import MediaCaptioner
from milihu.faces.media import MediaSource
from milihu.types.caption import CaptionOutput, CaptionPosition, CaptionStyle
from milihu.types.default.caption import (
    DEFAULT_CAPTION_POSITION,
    DEFAULT_CAPTION_STYLE,
)
from milihu.types.default.payload import (
    DEFAULT_FILE_SIZE_LIMIT_BYTES,
    DEFAULT_PAYLOAD_OVERHEAD_BYTES,
)
from milihu.types.registry import BotClient

_FAKE_BOT = cast(BotClient, object())


def _as_ctx(ctx: object) -> commands.Context[BotClient]:
    return cast(commands.Context[BotClient], ctx)


def _as_message(msg: object) -> Message:
    return cast(Message, msg)


def _as_attachment(att: object) -> Attachment:
    return cast(Attachment, att)


def _make_cog(captioner: MediaCaptioner) -> CaptionCog:
    cog = CaptionCog(_FAKE_BOT, captioner=captioner)
    cog.caption.cog = cog
    return cog


@dataclass
class FakeAttachment:
    filename: str = "meme.png"
    content_type: str | None = "image/png"
    size: int = 128
    payload: bytes = b"png-bytes"

    async def read(self) -> bytes:
        return self.payload


class FakeCaptioner:
    def __init__(
        self,
        *,
        supported: bool = True,
        output: CaptionOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        self.supported = supported
        self.output = output or CaptionOutput(
            filename="meme_captioned.png",
            data=b"captioned",
            extension=".png",
        )
        self.error = error
        self.calls: list[
            tuple[MediaSource | str, str, CaptionPosition, CaptionStyle, int | None]
        ] = []

    def is_supported(self, source: MediaSource) -> bool:
        return self.supported

    async def caption(
        self,
        source: MediaSource | str,
        text: str,
        *,
        position: CaptionPosition = DEFAULT_CAPTION_POSITION,
        style: CaptionStyle = DEFAULT_CAPTION_STYLE,
        max_bytes: int | None = None,
    ) -> CaptionOutput:
        self.calls.append((source, text, position, style, max_bytes))
        if self.error is not None:
            raise self.error
        return self.output


class FakeMessage:
    def __init__(
        self,
        attachments: list[FakeAttachment] | None = None,
        *,
        embeds: list[Embed] | None = None,
        content: str = "",
    ) -> None:
        self.attachments = attachments or []
        self.embeds = embeds or []
        self.content = content
        self.reference: Any = None


class FakeContext:
    guild: Any = None
    interaction: Any = None

    def __init__(
        self,
        attachments: list[FakeAttachment] | None = None,
        *,
        embeds: list[Embed] | None = None,
        content: str = "",
    ) -> None:
        self.message = FakeMessage(attachments, embeds=embeds, content=content)
        self.in_typing = False
        self.reply_kwargs: dict[str, Any] | None = None

    def typing(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        self.in_typing = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.in_typing = False

    async def reply(self, content: str | None = None, **kwargs: Any) -> Any:
        if content is not None:
            kwargs["content"] = content
        self.reply_kwargs = kwargs
        return object()


class CaptionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_command_uses_default_top_bar_and_replies_with_view(
        self,
    ) -> None:
        attachment = FakeAttachment(filename="cat.jpg", content_type="image/jpeg")
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="cat_captioned.png",
                data=b"png-out",
                extension=".png",
            )
        )
        cog = _make_cog(captioner)
        ctx = FakeContext(attachments=[attachment])

        await cog.caption(_as_ctx(ctx), text="hello world")

        self.assertEqual(len(captioner.calls), 1)
        _, text, position, style, max_bytes = captioner.calls[0]
        self.assertEqual(text, "hello world")
        self.assertEqual(position, "top")
        self.assertEqual(style, "bar")
        self.assertEqual(
            max_bytes,
            DEFAULT_FILE_SIZE_LIMIT_BYTES - DEFAULT_PAYLOAD_OVERHEAD_BYTES,
        )

        assert ctx.reply_kwargs is not None
        self.assertEqual(ctx.reply_kwargs["file"].filename, "cat_captioned.png")
        view = ctx.reply_kwargs["view"]
        self.assertIsInstance(view, LayoutView)
        container = view.children[0]
        self.assertIsInstance(container, Container)
        self.assertIsInstance(container.children[0], TextDisplay)
        self.assertIsInstance(container.children[1], MediaGallery)

    async def test_caption_command_forwards_explicit_position_and_style(self) -> None:
        attachment = FakeAttachment(filename="dance.gif", content_type="image/gif")
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="dance_captioned.gif",
                data=b"gif-out",
                extension=".gif",
            )
        )
        cog = _make_cog(captioner)
        ctx = FakeContext(attachments=[attachment])

        await cog.caption(
            _as_ctx(ctx),
            position="bottom",
            style="overlay",
            text="nice move",
        )

        _, text, position, style, _ = captioner.calls[0]
        self.assertEqual((text, position, style), ("nice move", "bottom", "overlay"))
        assert ctx.reply_kwargs is not None
        self.assertEqual(ctx.reply_kwargs["file"].filename, "dance_captioned.gif")

    async def test_caption_slash_command_accepts_attachment_parameter(self) -> None:
        attachment = FakeAttachment(filename="upload.png", content_type="image/png")
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="upload_captioned.png",
                data=b"png-out",
                extension=".png",
            )
        )
        cog = _make_cog(captioner)
        ctx = FakeContext(attachments=[])

        assert cog.caption.app_command is not None
        param_types = {p.name: p.type for p in cog.caption.app_command.parameters}
        self.assertEqual(param_types["attachment"], AppCommandOptionType.attachment)

        await cog.caption(
            _as_ctx(ctx),
            attachment=_as_attachment(attachment),
            text="slash upload",
        )

        self.assertEqual(len(captioner.calls), 1)
        self.assertIs(captioner.calls[0][0], attachment)
        self.assertEqual(captioner.calls[0][1], "slash upload")

    def test_gif_slash_command_exposes_attachment_option(self) -> None:
        registry = build_cog_registry(None)
        gif_cog = registry[GifCog](_FAKE_BOT)
        assert isinstance(gif_cog, GifCog)
        assert gif_cog.to_gif.app_command is not None
        param_types = {p.name: p.type for p in gif_cog.to_gif.app_command.parameters}
        self.assertEqual(param_types["attachment"], AppCommandOptionType.attachment)
        self.assertEqual(param_types["url"], AppCommandOptionType.string)

    async def test_caption_command_accepts_gif_embed_url(self) -> None:
        embed = Embed.from_dict(
            {
                "type": "gifv",
                "url": "https://klipy.com/gifs/momona-lady-jewelpet",
                "video": {"url": "https://static.klipy.com/momona.mp4"},
                "thumbnail": {"url": "https://static.klipy.com/momona.jpg"},
            }
        )
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="momona_captioned.gif",
                data=b"gif-out",
                extension=".gif",
            )
        )
        cog = _make_cog(captioner)
        ctx = FakeContext(embeds=[embed])

        await cog.caption(_as_ctx(ctx), text="jewelpet")

        self.assertEqual(len(captioner.calls), 1)
        self.assertEqual(captioner.calls[0][0], "https://static.klipy.com/momona.mp4")

    async def test_caption_command_accepts_plain_gif_link_without_embed(self) -> None:
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="momona-lady-jewelpet_captioned.gif",
                data=b"gif-out",
                extension=".gif",
            )
        )
        cog = _make_cog(captioner)
        ctx = FakeContext(
            content="https://klipy.com/gifs/momona-lady-jewelpet",
        )

        await cog.caption(_as_ctx(ctx), text="jewelpet")

        self.assertEqual(len(captioner.calls), 1)
        self.assertEqual(
            captioner.calls[0][0],
            "https://klipy.com/gifs/momona-lady-jewelpet",
        )

    async def test_caption_command_rejects_missing_or_unsupported_attachment(
        self,
    ) -> None:
        captioner = FakeCaptioner(supported=False)
        cog = _make_cog(captioner)
        ctx = FakeContext(attachments=[])

        await cog.caption(_as_ctx(ctx), text="hello")

        self.assertEqual(captioner.calls, [])
        assert ctx.reply_kwargs is not None
        self.assertTrue(ctx.reply_kwargs["ephemeral"])
        self.assertIn("valid image, GIF, video, or link", ctx.reply_kwargs["content"])

    async def test_caption_command_handles_runtime_error_and_value_error(self) -> None:
        attachment = FakeAttachment()

        value_cog = _make_cog(
            FakeCaptioner(error=ValueError("Caption text cannot be empty."))
        )
        ctx = FakeContext(attachments=[attachment])
        await value_cog.caption(_as_ctx(ctx), text="   ")
        assert ctx.reply_kwargs is not None
        self.assertEqual(ctx.reply_kwargs["content"], "Caption text cannot be empty.")
        self.assertTrue(ctx.reply_kwargs["ephemeral"])

        runtime_cog = _make_cog(FakeCaptioner(error=RuntimeError("ffmpeg failed")))
        ctx = FakeContext(attachments=[attachment])
        await runtime_cog.caption(_as_ctx(ctx), text="boom")
        assert ctx.reply_kwargs is not None
        self.assertEqual(ctx.reply_kwargs["content"], "Failed to caption the file.")

    async def test_context_menu_rejects_message_without_supported_media(
        self,
    ) -> None:
        cog = _make_cog(FakeCaptioner(supported=False))
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        message = FakeMessage(attachments=[])

        await cog.open_caption_modal(
            cast(Interaction[BotClient], interaction),
            _as_message(message),
        )

        interaction.response.send_message.assert_awaited_once()
        interaction.response.send_modal.assert_not_awaited()

    async def test_context_menu_opens_modal_for_gif_link_message(self) -> None:
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="momona-lady-jewelpet_captioned.gif",
                data=b"gif-bytes",
                extension=".gif",
            )
        )
        cog = _make_cog(captioner)

        interaction = MagicMock()
        interaction.guild = None
        interaction.filesize_limit = DEFAULT_FILE_SIZE_LIMIT_BYTES
        interaction.response.send_modal = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        link = "https://klipy.com/gifs/momona-lady-jewelpet"
        await cog.open_caption_modal(
            cast(Interaction[BotClient], interaction),
            _as_message(FakeMessage(content=link)),
        )

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, Modal)

        await cog.handle_modal_submit(
            cast(Interaction[Client], interaction),
            link,
            "modal text",
            position="bottom",
            style="bar",
        )

        self.assertEqual(captioner.calls[0][0], link)
        send_kwargs = interaction.followup.send.call_args.kwargs
        self.assertEqual(
            send_kwargs["file"].filename,
            "momona-lady-jewelpet_captioned.gif",
        )

    async def test_context_menu_opens_modal_and_submits_caption(self) -> None:
        attachment = FakeAttachment(filename="clip.gif", content_type="image/gif")
        captioner = FakeCaptioner(
            output=CaptionOutput(
                filename="clip_captioned.gif",
                data=b"gif-bytes",
                extension=".gif",
            )
        )
        cog = _make_cog(captioner)

        interaction = MagicMock()
        interaction.guild = None
        interaction.filesize_limit = DEFAULT_FILE_SIZE_LIMIT_BYTES
        interaction.response.send_modal = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.open_caption_modal(
            cast(Interaction[BotClient], interaction),
            _as_message(FakeMessage(attachments=[attachment])),
        )

        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, Modal)
        self.assertEqual(modal.title, "Add Caption")

        await cog.handle_modal_submit(
            cast(Interaction[Client], interaction),
            _as_attachment(attachment),
            "modal text",
            position="bottom",
            style="bar",
        )

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        self.assertEqual(len(captioner.calls), 1)
        _, text, position, style, _ = captioner.calls[0]
        self.assertEqual((text, position, style), ("modal text", "bottom", "bar"))
        interaction.followup.send.assert_awaited_once()
        send_kwargs = interaction.followup.send.call_args.kwargs
        self.assertEqual(send_kwargs["file"].filename, "clip_captioned.gif")
        self.assertIsInstance(send_kwargs["view"], LayoutView)

    async def test_modal_submit_handles_payload_too_large(self) -> None:
        attachment = FakeAttachment()
        captioner = FakeCaptioner(error=PayloadTooLargeError(1024, size=2048))
        cog = _make_cog(captioner)

        interaction = MagicMock()
        interaction.guild = None
        interaction.filesize_limit = DEFAULT_FILE_SIZE_LIMIT_BYTES
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        await cog.handle_modal_submit(
            cast(Interaction[Client], interaction),
            _as_attachment(attachment),
            "too large",
        )

        interaction.followup.send.assert_awaited_once()
        view = interaction.followup.send.call_args.kwargs["view"]
        self.assertIsInstance(view, LayoutView)

    async def test_cog_load_and_unload_register_context_menu(self) -> None:
        bot = MagicMock()
        bot.tree.add_command = MagicMock()
        bot.tree.remove_command = MagicMock()
        cog = CaptionCog(cast(BotClient, bot), captioner=FakeCaptioner())

        await cog.cog_load()
        bot.tree.add_command.assert_called_once_with(cog.ctx_menu)

        await cog.cog_unload()
        bot.tree.remove_command.assert_called_once_with(
            cog.ctx_menu.name,
            type=cog.ctx_menu.type,
        )

    def test_cog_registry_wires_caption_cog(self) -> None:
        registry = build_cog_registry(None)
        self.assertIn(CaptionCog, registry)
        cog = registry[CaptionCog](_FAKE_BOT)
        self.assertIsInstance(cog, CaptionCog)

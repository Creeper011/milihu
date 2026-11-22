import unittest
from types import SimpleNamespace
from typing import Any, Self, cast, override

from discord import AllowedMentions, ButtonStyle, Intents, Member, VoiceChannel
from discord.ext import commands
from discord.ui import (
    ActionRow,
    Button,
    Container,
    LayoutView,
    Section,
    Separator,
    TextDisplay,
)
from pydantic import HttpUrl, TypeAdapter

from milihu.app.registry import build_cog_registry
from milihu.discord.cogs.music import MusicCog
from milihu.discord.types.bot import BotClient
from milihu.exceptions.voice import VoiceError
from milihu.types.default.voice import DEFAULT_PLAYBACK_OPTIONS
from milihu.types.voice import (
    EnqueueResult,
    LoopMode,
    PlaybackOptions,
    PlayRequest,
    QueueSnapshot,
    Track,
    TrackStartCallback,
)
from milihu.usecases.faces.media import MediaBackend

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


class FakeMusicPlayer:
    def __init__(self) -> None:
        self.on_track_start: TrackStartCallback | None = None
        self.enqueued: list[tuple[int, int | None, str, int]] = []
        self.skipped: list[tuple[int, int | None]] = []
        self.stopped: list[tuple[int, int | None]] = []
        self.closed = False
        self.loop_mode = LoopMode.OFF
        self.playback = DEFAULT_PLAYBACK_OPTIONS
        self.speed_calls: list[tuple[int, int | None, float | None, bool | None]] = []
        self.started_immediately = True
        self.track_title = "Test Song"
        self.enqueued_count = 1
        self.playlist_title: str | None = None
        self.warning: str | None = None
        self.raise_on_skip: Exception | None = None
        self.is_paused = False

    def set_on_track_start(self, callback: TrackStartCallback | None) -> None:
        self.on_track_start = callback

    async def enqueue(
        self,
        guild_id: int,
        channel_id: int | None,
        url: PlayRequest | str,
        *,
        requested_by: int,
    ) -> EnqueueResult:
        raw_url = str(url.url) if isinstance(url, PlayRequest) else url
        self.enqueued.append((guild_id, channel_id, raw_url, requested_by))
        track = Track(
            source_url=_URL_ADAPTER.validate_python(
                raw_url
                if raw_url.startswith("https://")
                else "https://example.com/result"
            ),
            title=self.track_title,
            requested_by=requested_by,
        )
        return EnqueueResult(
            track=track,
            position=0 if self.started_immediately else 1,
            started_immediately=self.started_immediately,
            enqueued_count=self.enqueued_count,
            playlist_title=self.playlist_title,
            warning=self.warning,
        )

    async def skip(self, guild_id: int, channel_id: int | None) -> Track:
        if self.raise_on_skip is not None:
            raise self.raise_on_skip
        self.skipped.append((guild_id, channel_id))
        return Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/a"),
            title="Skipped *Song*",
            requested_by=1,
        )

    async def pause(self, guild_id: int, channel_id: int | None) -> Track:
        if self.is_paused:
            raise VoiceError("Playback is already paused.")
        self.is_paused = True
        return Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/a"),
            title="Paused Song",
            requested_by=1,
        )

    async def resume(self, guild_id: int, channel_id: int | None) -> Track:
        if not self.is_paused:
            raise VoiceError("Playback is not paused.")
        self.is_paused = False
        return Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/a"),
            title="Resumed Song",
            requested_by=1,
        )

    async def stop(self, guild_id: int, channel_id: int | None) -> None:
        self.stopped.append((guild_id, channel_id))

    async def set_loop(
        self, guild_id: int, channel_id: int | None, mode: LoopMode
    ) -> LoopMode:
        self.loop_mode = mode
        return mode

    async def set_speed(
        self,
        guild_id: int,
        channel_id: int | None,
        *,
        speed: float | None = None,
        preserve_pitch: bool | None = None,
    ) -> PlaybackOptions:
        self.speed_calls.append((guild_id, channel_id, speed, preserve_pitch))
        self.playback = PlaybackOptions(
            speed=self.playback.speed if speed is None else speed,
            preserve_pitch=(
                self.playback.preserve_pitch
                if preserve_pitch is None
                else preserve_pitch
            ),
        )
        return self.playback

    async def remove(
        self, guild_id: int, channel_id: int | None, position: int
    ) -> Track:
        return Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/b"),
            title="Removed Song",
            requested_by=1,
        )

    async def clear(self, guild_id: int, channel_id: int | None) -> int:
        return 2

    async def shuffle(self, guild_id: int, channel_id: int | None) -> int:
        return 2

    def get_queue(self, guild_id: int) -> QueueSnapshot:
        return QueueSnapshot(
            guild_id=guild_id,
            channel_id=100,
            current=Track(
                source_url=_URL_ADAPTER.validate_python("https://example.com/a"),
                title="Current *Song*",
                requested_by=42,
            ),
            upcoming=(
                Track(
                    source_url=_URL_ADAPTER.validate_python("https://example.com/b"),
                    title="Next _Song_",
                    requested_by=43,
                ),
            ),
            loop_mode=self.loop_mode,
            is_paused=self.is_paused,
            playback=self.playback,
        )

    async def aclose(self) -> None:
        self.closed = True


class FakeVoiceTextChannel(VoiceChannel):
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_kwargs: list[dict[str, Any]] = []

    @override
    async def send(self, content: Any = None, **kwargs: Any) -> Any:
        if content is not None:
            kwargs["content"] = content
        self.sent_kwargs.append(kwargs)
        return SimpleNamespace()


class FakeMember(Member):
    def __init__(self, user_id: int, voice_channel_id: int | None) -> None:
        self._user: Any = SimpleNamespace(id=user_id)
        self._voice = (
            SimpleNamespace(channel=SimpleNamespace(id=voice_channel_id))
            if voice_channel_id is not None
            else None
        )

    @property
    @override
    def voice(self) -> Any:
        return self._voice


class FakeContext:
    def __init__(
        self,
        *,
        guild_id: int = 1,
        channel_id: int = 50,
        voice_channel_id: int | None = 100,
        user_id: int = 42,
    ) -> None:
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.author = FakeMember(user_id, voice_channel_id)
        self.invoked_subcommand: Any = None
        self.replies: list[dict[str, Any]] = []
        self.typing_calls: list[bool] = []

    def typing(self, *, ephemeral: bool = False) -> Self:
        self.typing_calls.append(ephemeral)
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def reply(self, content: str | None = None, **kwargs: Any) -> None:
        if content is not None:
            kwargs["content"] = content
        self.replies.append(kwargs)


class FakeGuild:
    def __init__(self, guild_id: int, voice_channel: FakeVoiceTextChannel) -> None:
        self.id = guild_id
        self._voice_channel = voice_channel

    def get_channel(self, channel_id: int) -> FakeVoiceTextChannel | None:
        return self._voice_channel if channel_id == self._voice_channel.id else None


class FakeBot:
    def __init__(self, voice_channel: FakeVoiceTextChannel | None = None) -> None:
        self.voice_channel = voice_channel
        self.listeners: list[Any] = []

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        if self.voice_channel is None:
            return None
        return FakeGuild(guild_id, self.voice_channel)

    def add_listener(self, func: Any, name: str = "") -> None:
        self.listeners.append((name, func))

    def remove_listener(self, func: Any, name: str = "") -> None:
        self.listeners = [item for item in self.listeners if item != (name, func)]


def _as_bot(bot: object) -> BotClient:
    return cast(BotClient, bot)


def _as_ctx(ctx: object) -> commands.Context[BotClient]:
    return cast(commands.Context[BotClient], ctx)


def _make_cog(bot: FakeBot, player: FakeMusicPlayer) -> MusicCog:
    cog = MusicCog(_as_bot(bot), player)
    cog.music.cog = cog
    cog.play.cog = cog
    cog.queue.cog = cog
    cog.loop.cog = cog
    cog.speed.cog = cog
    cog.pitch.cog = cog
    cog.skip.cog = cog
    return cog


def _extract_view_text(view: LayoutView) -> str:
    parts: list[str] = []
    for item in view.children:
        if isinstance(item, Container):
            for child in item.children:
                if isinstance(child, TextDisplay):
                    parts.append(child.content)
                elif isinstance(child, Section):
                    for sub in child.children:
                        if isinstance(sub, TextDisplay):
                            parts.append(sub.content)
    return "\n".join(parts)


def _is_no_mentions(value: object) -> bool:
    return (
        isinstance(value, AllowedMentions)
        and value.to_dict() == AllowedMentions.none().to_dict()
    )


class MusicCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_more_short_music_commands_are_registered_and_work(self) -> None:
        bot = commands.Bot(command_prefix="m$", intents=Intents.default())
        player = FakeMusicPlayer()
        try:
            await bot.add_cog(MusicCog(_as_bot(bot), player))
            ctx = _as_ctx(FakeContext())
            for name, args in (
                ("mpa", ()),
                ("mr", ()),
                ("mq", ()),
                ("ml", ("queue",)),
                ("mrm", (1,)),
                ("mc", ()),
                ("msh", ()),
            ):
                command = bot.get_command(name)
                self.assertIsNotNone(command, name)
                assert command is not None
                await command(ctx, *args)

            self.assertFalse(player.is_paused)
            self.assertIs(player.loop_mode, LoopMode.QUEUE)
            self.assertEqual(
                [reply.get("content") for reply in cast(FakeContext, ctx).replies],
                [
                    "Paused **Paused Song**.",
                    "Resumed **Resumed Song**.",
                    None,
                    "Loop mode set to `queue`.",
                    "Removed **Removed Song** from the queue.",
                    "Cleared 2 track(s) from the queue.",
                    "Shuffled 2 track(s) in the queue.",
                ],
            )
        finally:
            await bot.close()

    async def test_short_music_commands_are_registered_and_work(self) -> None:
        bot = commands.Bot(command_prefix="m$", intents=Intents.default())
        player = FakeMusicPlayer()
        cog = MusicCog(_as_bot(bot), player)
        try:
            await bot.add_cog(cog)
            ctx = FakeContext(guild_id=1, channel_id=50, voice_channel_id=100)

            play = bot.get_command("mp")
            skip = bot.get_command("ms")
            stop = bot.get_command("mst")
            self.assertIsNotNone(play)
            self.assertIsNotNone(skip)
            self.assertIsNotNone(stop)
            assert play is not None and skip is not None and stop is not None

            await play(_as_ctx(ctx), url="some song title")
            await skip(_as_ctx(ctx))
            await stop(_as_ctx(ctx))

            self.assertEqual(player.enqueued, [(1, 100, "some song title", 42)])
            self.assertEqual(player.skipped, [(1, 100)])
            self.assertEqual(player.stopped, [(1, 100)])
            self.assertIn("Playing **Test Song**", ctx.replies[0]["content"])
            self.assertIn("Skipped **Skipped \\*Song\\***", ctx.replies[1]["content"])
            self.assertEqual(
                ctx.replies[2]["content"], "Stopped playback and disconnected."
            )
        finally:
            await bot.close()

    async def test_play_forwards_search_query(self) -> None:
        player = FakeMusicPlayer()
        cog = _make_cog(FakeBot(), player)
        ctx = FakeContext(guild_id=1, channel_id=50, voice_channel_id=100, user_id=42)
        await cog.play(_as_ctx(ctx), url="some song title")
        self.assertEqual(player.enqueued, [(1, 100, "some song title", 42)])
        self.assertIs(
            cog.play.params["url"].kind, commands.Parameter.KEYWORD_ONLY
        )

    def test_registry_wires_music_cog(self) -> None:
        bot = FakeBot()
        registry = build_cog_registry(cast(MediaBackend, object()))
        cog = registry[MusicCog](_as_bot(bot))
        self.assertIsInstance(cog, MusicCog)
        self.assertEqual(len(bot.listeners), 1)

    async def test_play_escaping_mentions_and_ephemeral_typing(self) -> None:
        voice_channel = FakeVoiceTextChannel(100)
        bot = FakeBot(voice_channel)
        player = FakeMusicPlayer()
        player.track_title = "**Bold** _Italic_ @everyone"
        cog = _make_cog(bot, player)

        # Regular text channel: non-ephemeral typing, escaped markdown, no mentions.
        ctx = FakeContext(guild_id=1, channel_id=50, voice_channel_id=100, user_id=42)
        await cog.play(_as_ctx(ctx), url="https://example.com/song")
        self.assertEqual(ctx.typing_calls, [False])
        self.assertEqual(player.enqueued, [(1, 100, "https://example.com/song", 42)])
        self.assertEqual(ctx.replies[-1]["ephemeral"], False)
        self.assertIn(r"\*\*Bold\*\* \_Italic\_", ctx.replies[-1]["content"])
        self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

        # Voice channel chat: ephemeral typing matches ephemeral reply.
        vc_ctx = FakeContext(
            guild_id=1, channel_id=100, voice_channel_id=100, user_id=42
        )
        await cog.play(_as_ctx(vc_ctx), url="https://example.com/song2")
        self.assertEqual(vc_ctx.typing_calls, [True])
        self.assertEqual(vc_ctx.replies[-1]["ephemeral"], True)

        # Now Playing card uses escaped title and disables mentions.
        assert player.on_track_start is not None
        track = Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/song"),
            title="*Trap* @everyone",
            requested_by=42,
        )
        await player.on_track_start(1, 100, track)
        self.assertEqual(len(voice_channel.sent_kwargs), 1)
        sent = voice_channel.sent_kwargs[0]
        self.assertIsInstance(sent["view"], LayoutView)
        self.assertTrue(_is_no_mentions(sent["allowed_mentions"]))
        now_playing_text = _extract_view_text(sent["view"])
        self.assertIn("### 🎵 Now Playing", now_playing_text)
        self.assertIn(r"**\*Trap\*", now_playing_text)
        self.assertIn("-# Requested by: <@42>", now_playing_text)

        # Group without subcommand shows queue with escaped titles & no mentions.
        await cog.music(_as_ctx(ctx))
        queue_reply = ctx.replies[-1]
        self.assertIsInstance(queue_reply["view"], LayoutView)
        self.assertTrue(_is_no_mentions(queue_reply["allowed_mentions"]))
        queue_text = _extract_view_text(queue_reply["view"])
        self.assertIn(r"**Current \*Song\***", queue_text)
        self.assertIn(r"Next \_Song\_", queue_text)

        # Loop command converts Literal argument to LoopMode.
        await cog.loop(_as_ctx(ctx), "queue")
        self.assertIs(player.loop_mode, LoopMode.QUEUE)

        await cog.cog_unload()
        self.assertTrue(player.closed)

    async def test_command_errors_reply_ephemerally_without_mentions(self) -> None:
        cog = _make_cog(FakeBot(), FakeMusicPlayer())
        ctx = FakeContext()

        wrapped = commands.CommandInvokeError(VoiceError("Join a voice channel."))
        await cog.cog_command_error(_as_ctx(ctx), wrapped)
        self.assertEqual(ctx.replies[-1]["content"], "Join a voice channel.")
        self.assertIs(ctx.replies[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

        # BadArgument also replies ephemerally.
        await cog.cog_command_error(
            _as_ctx(ctx), commands.BadArgument("Invalid position.")
        )
        self.assertEqual(ctx.replies[-1]["content"], "Invalid position.")
        self.assertIs(ctx.replies[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

        # BadLiteralArgument and MissingRequiredArgument also reply ephemerally.
        param = commands.Parameter("mode", commands.Parameter.POSITIONAL_OR_KEYWORD)
        await cog.cog_command_error(
            _as_ctx(ctx),
            commands.BadLiteralArgument(param, ("off", "track", "queue"), [], "nope"),
        )
        self.assertIs(ctx.replies[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

        speed_param = commands.Parameter(
            "speed", commands.Parameter.POSITIONAL_OR_KEYWORD
        )
        await cog.cog_command_error(
            _as_ctx(ctx),
            commands.MissingRequiredArgument(speed_param),
        )
        self.assertIs(ctx.replies[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

    async def test_play_formats_playlist_summary_and_fallback_warning(self) -> None:
        player = FakeMusicPlayer()
        cog = _make_cog(FakeBot(), player)
        ctx = FakeContext(guild_id=1, channel_id=50, voice_channel_id=100, user_id=42)

        # Playlist starting immediately escapes title and playlist name.
        player.track_title = "First *Track*"
        player.enqueued_count = 5
        player.playlist_title = "Mix _Indie_ @everyone"
        player.started_immediately = True
        await cog.play(_as_ctx(ctx), url="https://youtu.be/abc?list=RD123")
        reply = ctx.replies[-1]
        self.assertIn(r"Playing **First \*Track\***", reply["content"])
        self.assertIn(r"queued 4 more track(s) from **Mix \_Indie\_", reply["content"])
        self.assertTrue(_is_no_mentions(reply["allowed_mentions"]))

        # Playlist queued while already playing.
        player.started_immediately = False
        await cog.play(
            _as_ctx(ctx), url="https://www.youtube.com/playlist?list=PL123"
        )
        reply2 = ctx.replies[-1]
        self.assertIn(
            r"Queued 5 tracks from **Mix \_Indie\_ @everyone** "
            "starting at position #1.",
            reply2["content"],
        )
        self.assertTrue(_is_no_mentions(reply2["allowed_mentions"]))

        # Single-track fallback displays warning subtext.
        player.enqueued_count = 1
        player.playlist_title = None
        player.started_immediately = True
        player.warning = (
            "Could not load the playlist; playing the single track instead."
        )
        await cog.play(
            _as_ctx(ctx), url="https://www.youtube.com/watch?v=abc&list=WL"
        )
        reply3 = ctx.replies[-1]
        self.assertIn(
            "\n-# Could not load the playlist; playing the single track instead.",
            reply3["content"],
        )

    async def test_now_playing_view_components_v2_and_queue_button(self) -> None:
        player = FakeMusicPlayer()
        cog = _make_cog(FakeBot(), player)
        track = Track(
            source_url=_URL_ADAPTER.validate_python("https://example.com/live"),
            title="Live Radio",
            requested_by=99,
            is_live=True,
        )
        view = cog.build_now_playing_view(1, track)
        container = next(c for c in view.children if isinstance(c, Container))
        self.assertEqual(container.accent_colour, 0xED4245)
        self.assertFalse(
            any(isinstance(c, (ActionRow, Separator)) for c in container.children)
        )

        sections = [c for c in container.children if isinstance(c, Section)]
        self.assertEqual(len(sections), 2)

        source_btn = sections[0].accessory
        self.assertIsInstance(source_btn, Button)
        assert isinstance(source_btn, Button)
        self.assertEqual(source_btn.style, ButtonStyle.link)
        self.assertEqual(source_btn.url, "https://example.com/live")

        queue_btn = sections[1].accessory
        self.assertIsInstance(queue_btn, Button)
        assert isinstance(queue_btn, Button)
        self.assertEqual(queue_btn.style, ButtonStyle.secondary)
        self.assertEqual(queue_btn.label, "Queue")

        text = _extract_view_text(view)
        self.assertIn("### 🔴 Now Playing • LIVE", text)
        self.assertIn("**Live Radio**", text)
        self.assertIn("-# Requested by: <@99>", text)

        sent_responses: list[dict[str, Any]] = []

        async def send_message(content: str | None = None, **kwargs: Any) -> None:
            sent_responses.append({"content": content, **kwargs})

        interaction = cast(
            Any,
            SimpleNamespace(
                guild_id=1,
                response=SimpleNamespace(send_message=send_message),
            ),
        )
        await queue_btn.callback(interaction)
        self.assertIsInstance(sent_responses[-1]["view"], LayoutView)
        self.assertIs(sent_responses[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(sent_responses[-1]["allowed_mentions"]))

        dm_interaction = cast(
            Any,
            SimpleNamespace(
                guild_id=None,
                response=SimpleNamespace(send_message=send_message),
            ),
        )
        await queue_btn.callback(dm_interaction)
        self.assertEqual(
            sent_responses[-1]["content"],
            "Music controls can only be used in a server.",
        )
        self.assertIs(sent_responses[-1]["ephemeral"], True)
        self.assertTrue(_is_no_mentions(sent_responses[-1]["allowed_mentions"]))

    async def test_speed_and_pitch_commands_with_short_prefixes(self) -> None:
        bot = commands.Bot(command_prefix="m$", intents=Intents.default())
        player = FakeMusicPlayer()
        cog = MusicCog(_as_bot(bot), player)
        try:
            await bot.add_cog(cog)
            ctx = FakeContext(guild_id=1, channel_id=50, voice_channel_id=100)

            # `music speed` requires `speed`; `preserve_pitch` is optional.
            self.assertTrue(cog.speed.clean_params["speed"].required)
            self.assertFalse(cog.speed.clean_params["preserve_pitch"].required)

            # Set speed and preserve_pitch via hybrid subcommand.
            await cog.speed(_as_ctx(ctx), speed=1.5, preserve_pitch=False)
            self.assertEqual(player.speed_calls, [(1, 100, 1.5, False)])
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Playback speed set to `1.5x` (preserve pitch: `off`).",
            )
            self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

            # Queue card footer displays active speed & pitch mode.
            queue_view = cog.build_queue_view(player.get_queue(1))
            self.assertIn(
                "Speed: `1.5x` (pitch shifted)", _extract_view_text(queue_view)
            )

            # Short prefix command `msp` only takes required `speed`.
            msp = bot.get_command("msp")
            self.assertIsNotNone(msp)
            assert msp is not None
            self.assertEqual(list(msp.clean_params), ["speed"])
            self.assertTrue(msp.clean_params["speed"].required)
            await msp(_as_ctx(ctx), 0.75)
            self.assertEqual(player.speed_calls[-1], (1, 100, 0.75, None))
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Playback speed set to `0.75x` (preserve pitch: `off`).",
            )

            # Hybrid subcommand `music pitch` toggles when omitted or sets explicitly.
            self.assertIn("pp", cog.pitch.aliases)
            self.assertFalse(cog.pitch.clean_params["preserve"].required)
            await cog.pitch(_as_ctx(ctx))
            self.assertEqual(player.speed_calls[-1], (1, 100, None, True))
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Preserve pitch set to `on` (speed: `0.75x`).",
            )
            self.assertTrue(_is_no_mentions(ctx.replies[-1]["allowed_mentions"]))

            await cog.pitch(_as_ctx(ctx), preserve=False)
            self.assertEqual(player.speed_calls[-1], (1, 100, None, False))
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Preserve pitch set to `off` (speed: `0.75x`).",
            )

            # Short prefix command `mpp` delegates to `pitch` (toggle & explicit).
            mpp = bot.get_command("mpp")
            self.assertIsNotNone(mpp)
            assert mpp is not None
            self.assertEqual(list(mpp.clean_params), ["preserve"])
            self.assertFalse(mpp.clean_params["preserve"].required)
            await mpp(_as_ctx(ctx))
            self.assertEqual(player.speed_calls[-1], (1, 100, None, True))
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Preserve pitch set to `on` (speed: `0.75x`).",
            )

            await mpp(_as_ctx(ctx), False)
            self.assertEqual(player.speed_calls[-1], (1, 100, None, False))
            self.assertEqual(
                ctx.replies[-1]["content"],
                "Preserve pitch set to `off` (speed: `0.75x`).",
            )
        finally:
            await bot.close()

    async def test_speed_prefix_conversion_and_slash_range(self) -> None:
        bot = commands.Bot(command_prefix="m$", intents=Intents.default())
        player = FakeMusicPlayer()
        cog = MusicCog(_as_bot(bot), player)
        try:
            await bot.add_cog(cog)
            msp = bot.get_command("msp")
            music_speed = bot.get_command("music speed")
            assert msp is not None
            assert music_speed is not None

            for cmd in (msp, music_speed):
                param = cmd.clean_params["speed"]
                ctx = _as_ctx(
                    SimpleNamespace(
                        command=cmd,
                        current_parameter=param,
                        bot=bot,
                    )
                )
                converted = await commands.run_converters(
                    ctx, param.converter, "0.75", param
                )
                self.assertEqual(converted, 0.75)

                with self.assertRaises(commands.RangeError):
                    await commands.run_converters(ctx, param.converter, "5.0", param)

                with self.assertRaises(commands.BadArgument):
                    await commands.run_converters(ctx, param.converter, "fast", param)

            assert cog.speed.app_command is not None
            slash_speed = cog.speed.app_command.parameters[0]
            self.assertEqual(slash_speed.name, "speed")
            self.assertEqual(slash_speed.min_value, 0.25)
            self.assertEqual(slash_speed.max_value, 4.0)
        finally:
            await bot.close()

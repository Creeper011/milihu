import asyncio
import threading
import unittest
from types import SimpleNamespace
from typing import Any, cast, override

from discord import (
    AudioSource,
    ClientException,
    Member,
    StageChannel,
    VoiceChannel,
    VoiceClient,
    VoiceState,
)

from milihu.discord.types.bot import BotClient
from milihu.exceptions.voice import VoiceError
from milihu.services.discord_voice import DiscordVoiceManager
from milihu.types.default.voice import (
    DEFAULT_FFMPEG_BEFORE_OPTIONS,
    DEFAULT_PLAYBACK_OPTIONS,
)
from milihu.types.voice import PlaybackOptions, SeekOffset


def _as_bot(bot: object) -> BotClient:
    return cast(BotClient, bot)


def _as_member(member: object) -> Member:
    return cast(Member, member)


def _as_voice_state(state: object) -> VoiceState:
    return cast(VoiceState, state)


class FakeAudioSource(AudioSource):
    def __init__(self) -> None:
        self.cleaned_up = False

    @override
    def read(self) -> bytes:
        return b""

    @override
    def cleanup(self) -> None:
        self.cleaned_up = True


class FakeVoiceClient(VoiceClient):
    """Test double passing isinstance(..., VoiceClient) without network sockets."""

    def __init__(self, channel: Any) -> None:
        self.channel: Any = channel
        self.connected = True
        self._playing = False
        self._paused = False
        self.moved_to: list[Any] = []
        self.played_sources: list[AudioSource] = []
        self.last_after: Any = None
        self.stopped = 0
        self.disconnect_calls: list[bool] = []
        self.raise_on_play: Exception | None = None
        self.raise_on_move: Exception | None = None

    @override
    def is_connected(self) -> bool:
        return self.connected

    @override
    def is_playing(self) -> bool:
        return self._playing

    @override
    def is_paused(self) -> bool:
        return self._paused

    @override
    async def move_to(self, channel: Any, *, timeout: float | None = 30.0) -> None:
        if self.raise_on_move is not None:
            raise self.raise_on_move
        self.moved_to.append(channel)
        self.channel = channel

    @override
    async def disconnect(self, *, force: bool = False) -> None:
        self.disconnect_calls.append(force)
        self.connected = False
        self._playing = False
        self._paused = False

    @override
    def play(
        self,
        source: AudioSource,
        *,
        after: Any = None,
        **_: Any,
    ) -> None:
        if self.raise_on_play is not None:
            raise self.raise_on_play
        self.played_sources.append(source)
        self.last_after = after
        self._playing = True
        self._paused = False

    @override
    def stop(self) -> None:
        self.stopped += 1
        self._playing = False
        self._paused = False

    @override
    def pause(self) -> None:
        self._playing = False
        self._paused = True

    @override
    def resume(self) -> None:
        self._paused = False
        self._playing = True


class FakeVoiceChannel(VoiceChannel):
    """Test double passing isinstance(..., VoiceChannel)."""

    def __init__(
        self,
        channel_id: int,
        guild: "FakeGuild",
        members: list[Any] | None = None,
    ) -> None:
        self.id = channel_id
        self._guild = guild
        self.channel_members = members or []
        self.connect_calls: list[bool] = []
        self.edited_statuses: list[str | None] = []
        self.raise_on_connect: Exception | None = None

    @property
    @override
    def members(self) -> Any:
        return self.channel_members

    @override
    async def edit(self, *, reason: str | None = None, **options: Any) -> Any:
        if "status" in options:
            self.edited_statuses.append(options["status"])
        return self

    @override
    async def connect(
        self,
        *,
        timeout: float = 30.0,
        reconnect: bool = True,
        cls: Any = VoiceClient,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> Any:
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        self.connect_calls.append(self_deaf)
        vc = FakeVoiceClient(self)
        self._guild.voice_client = vc
        return vc


class FakeStageChannel(StageChannel):
    """Test double passing isinstance(..., StageChannel)."""

    def __init__(self, channel_id: int, guild: "FakeGuild") -> None:
        self.id = channel_id
        self._guild = guild
        self._members: list[Any] = []
        self.connect_calls: list[bool] = []

    @property
    @override
    def members(self) -> Any:
        return self._members

    @override
    async def connect(
        self,
        *,
        timeout: float = 30.0,
        reconnect: bool = True,
        cls: Any = VoiceClient,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> Any:
        self.connect_calls.append(self_deaf)
        vc = FakeVoiceClient(self)
        self._guild.voice_client = vc
        return vc


class FakeBotMember:
    def __init__(self) -> None:
        self.edit_calls: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.edit_calls.append(kwargs)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.channels: dict[int, Any] = {}
        self.voice_client: FakeVoiceClient | None = None
        self.me = FakeBotMember()

    def get_channel(self, channel_id: int) -> Any:
        return self.channels.get(channel_id)


class FakeBot:
    def __init__(self, bot_user_id: int = 999) -> None:
        self.user = SimpleNamespace(id=bot_user_id)
        self.guilds: dict[int, FakeGuild] = {}
        self.listeners: list[tuple[str, Any]] = []

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self.guilds.get(guild_id)

    def add_listener(self, func: Any, name: str = "") -> None:
        self.listeners.append((name, func))

    def remove_listener(self, func: Any, name: str = "") -> None:
        self.listeners = [item for item in self.listeners if item != (name, func)]


class DiscordVoiceManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_ffmpeg_protocol_whitelist_restricts_protocols(self) -> None:
        self.assertIn(
            "-protocol_whitelist http,https,tcp,tls,crypto",
            DEFAULT_FFMPEG_BEFORE_OPTIONS,
        )

    async def test_connect_move_and_non_bot_member_count(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(1)
        ch1 = FakeVoiceChannel(
            10,
            guild,
            members=[
                SimpleNamespace(id=1, bot=False),
                SimpleNamespace(id=2, bot=True),
                SimpleNamespace(id=3, bot=False),
            ],
        )
        ch2 = FakeVoiceChannel(20, guild)
        guild.channels = {10: ch1, 20: ch2}
        bot.guilds = {1: guild}

        manager = DiscordVoiceManager(_as_bot(bot))
        self.assertIsNone(manager.connected_channel_id(1))

        await manager.connect(1, 10)
        self.assertEqual(ch1.connect_calls, [True])
        self.assertEqual(manager.connected_channel_id(1), 10)
        self.assertEqual(manager.non_bot_member_count(1), 2)

        # Same channel is a no-op.
        await manager.connect(1, 10)
        self.assertEqual(len(ch1.connect_calls), 1)
        assert guild.voice_client is not None
        self.assertEqual(guild.voice_client.moved_to, [])

        # Different channel moves the existing voice client.
        await manager.connect(1, 20)
        self.assertEqual(guild.voice_client.moved_to, [ch2])
        self.assertEqual(manager.connected_channel_id(1), 20)

        # Channel status truncates to 500 chars and can be cleared even by explicit id.
        await manager.set_channel_status(1, "x" * 600)
        await manager.set_channel_status(1, None, channel_id=20)
        self.assertEqual(ch2.edited_statuses, ["x" * 500, None])
        await manager.aclose()

    async def test_zombie_voice_client_recovery_and_exception_translation(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(1)
        ch1 = FakeVoiceChannel(10, guild)
        ch2 = FakeVoiceChannel(20, guild)
        guild.channels = {10: ch1, 20: ch2}
        bot.guilds = {1: guild}

        # Stale disconnected VoiceClient is force-disconnected before reconnecting.
        zombie = FakeVoiceClient(ch1)
        zombie.connected = False
        guild.voice_client = zombie

        manager = DiscordVoiceManager(_as_bot(bot))
        await manager.connect(1, 10)
        self.assertEqual(zombie.disconnect_calls, [True])
        self.assertEqual(ch1.connect_calls, [True])
        assert guild.voice_client is not None
        self.assertIsNot(guild.voice_client, zombie)

        # move_to ClientException / TimeoutError translated to VoiceError.
        guild.voice_client.raise_on_move = ClientException("move failed")
        with self.assertRaisesRegex(VoiceError, "Could not move"):
            await manager.connect(1, 20)

        guild.voice_client = None
        ch1.raise_on_connect = TimeoutError("handshake timed out")
        with self.assertRaisesRegex(VoiceError, "Could not connect"):
            await manager.connect(1, 10)
        await manager.aclose()

    async def test_stage_channel_connect_unsuppresses_bot(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(1)
        stage = FakeStageChannel(30, guild)
        guild.channels = {30: stage}
        bot.guilds = {1: guild}

        manager = DiscordVoiceManager(_as_bot(bot))
        await manager.connect(1, 30)
        self.assertEqual(stage.connect_calls, [True])
        self.assertEqual(guild.me.edit_calls, [{"suppress": False}])
        await manager.aclose()

    async def test_play_marshals_after_callback_and_cleans_up_on_error(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(1)
        ch1 = FakeVoiceChannel(10, guild)
        guild.channels = {10: ch1}
        bot.guilds = {1: guild}

        sources: list[FakeAudioSource] = []
        factory_calls: list[tuple[str, PlaybackOptions, float]] = []

        def _make_source(
            url: str,
            *,
            playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
            seek_seconds: SeekOffset = 0.0,
        ) -> AudioSource:
            factory_calls.append((url, playback, seek_seconds))
            src = FakeAudioSource()
            sources.append(src)
            return src

        manager = DiscordVoiceManager(_as_bot(bot), audio_factory=_make_source)
        await manager.connect(1, 10)

        received: list[Exception | None] = []
        done = asyncio.Event()

        async def on_after(err: Exception | None) -> None:
            received.append(err)
            done.set()

        custom_playback = PlaybackOptions(speed=1.5, preserve_pitch=False)
        manager.play(
            1,
            "https://cdn.example/audio.opus",
            after=on_after,
            playback=custom_playback,
            seek_seconds=12.5,
        )
        assert guild.voice_client is not None
        self.assertEqual(guild.voice_client.played_sources, [sources[0]])
        self.assertEqual(
            factory_calls,
            [("https://cdn.example/audio.opus", custom_playback, 12.5)],
        )
        self.assertTrue(manager.is_playing(1))

        # Pause, resume, and stop delegate to the voice client.
        self.assertTrue(manager.pause(1))
        self.assertTrue(manager.is_paused(1))
        self.assertTrue(manager.resume(1))
        self.assertTrue(manager.is_playing(1))

        # Invoke VoiceClient's `after` callback from a separate thread.
        sentinel = RuntimeError("stream ended")
        vc = guild.voice_client
        worker = threading.Thread(target=lambda: vc.last_after(sentinel))
        worker.start()
        worker.join()

        await asyncio.wait_for(done.wait(), timeout=2.0)
        self.assertEqual(received, [sentinel])

        # vc.play ClientException cleans up source and translates to VoiceError.
        vc.raise_on_play = ClientException("Already playing audio.")
        with self.assertRaisesRegex(VoiceError, "Could not start playback"):
            manager.play(1, "https://cdn.example/b.opus", after=on_after)
        self.assertTrue(sources[-1].cleaned_up)
        await manager.aclose()

    async def test_play_raises_when_not_connected(self) -> None:
        bot = FakeBot()
        manager = DiscordVoiceManager(_as_bot(bot))
        with self.assertRaises(VoiceError):
            manager.play(1, "https://cdn.example/a", after=self._noop_after)
        await manager.aclose()

    async def test_voice_state_listeners_and_aclose(self) -> None:
        bot = FakeBot(bot_user_id=999)
        guild = FakeGuild(7)
        human = SimpleNamespace(id=123, bot=False, guild=guild)
        ch1 = FakeVoiceChannel(10, guild, members=[human])
        ch2 = FakeVoiceChannel(20, guild)
        guild.channels = {10: ch1, 20: ch2}
        bot.guilds = {7: guild}

        manager = DiscordVoiceManager(_as_bot(bot))
        await manager.connect(7, 10)

        disconnected_guilds: list[int] = []
        moved_events: list[tuple[int, int, int]] = []
        empty_events: list[tuple[int, bool]] = []

        async def on_disconnect(guild_id: int) -> None:
            disconnected_guilds.append(guild_id)

        async def on_move(guild_id: int, old_id: int, new_id: int) -> None:
            moved_events.append((guild_id, old_id, new_id))

        async def on_empty(guild_id: int, is_empty: bool) -> None:
            empty_events.append((guild_id, is_empty))

        manager.set_on_disconnect(on_disconnect)
        manager.set_on_channel_move(on_move)
        manager.set_on_channel_empty_changed(on_empty)
        self.assertEqual(len(bot.listeners), 1)
        _, listener = bot.listeners[0]

        before_ch1 = _as_voice_state(SimpleNamespace(channel=ch1))
        after_ch2 = _as_voice_state(SimpleNamespace(channel=ch2))
        after_out = _as_voice_state(SimpleNamespace(channel=None))

        # Human leaves ch1 -> channel becomes empty.
        ch1.channel_members = []
        await listener(_as_member(human), before_ch1, after_out)
        self.assertEqual(empty_events, [(7, True)])

        # Human rejoins ch1 -> channel no longer empty.
        ch1.channel_members = [human]
        await listener(_as_member(human), after_out, before_ch1)
        self.assertEqual(empty_events, [(7, True), (7, False)])

        # Mute/deafen in same channel does not fire empty event.
        await listener(_as_member(human), before_ch1, before_ch1)
        self.assertEqual(len(empty_events), 2)

        # Admin drags bot from ch1 to ch2.
        bot_member = _as_member(SimpleNamespace(id=999, bot=True, guild=guild))
        await listener(bot_member, before_ch1, after_ch2)
        self.assertEqual(moved_events, [(7, 10, 20)])

        # Bot disconnected externally.
        await listener(bot_member, after_ch2, after_out)
        self.assertEqual(disconnected_guilds, [7])

        # Reconnect and verify aclose stops/disconnects and removes listener.
        await manager.connect(7, 10)
        vc = guild.voice_client
        assert vc is not None
        await manager.aclose()
        self.assertEqual(bot.listeners, [])
        self.assertEqual(vc.disconnect_calls, [True])

    @staticmethod
    async def _noop_after(_: Exception | None) -> None:
        pass

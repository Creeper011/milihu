"""Discord voice channel connection and audio playback manager."""

import asyncio
import logging
from typing import Protocol

from discord import (
    AudioSource,
    ClientException,
    FFmpegOpusAudio,
    Forbidden,
    HTTPException,
    Member,
    StageChannel,
    VoiceChannel,
    VoiceClient,
    VoiceState,
)

from milihu.discord.types.bot import BotClient
from milihu.exceptions.voice import VoiceError
from milihu.services.ffmpeg.audio_options import build_ffmpeg_options
from milihu.types.default.voice import (
    DEFAULT_FFMPEG_BEFORE_OPTIONS,
    DEFAULT_PLAYBACK_OPTIONS,
    DISCORD_VOICE_STATUS_MAX_LENGTH,
)
from milihu.types.voice import (
    ChannelEmptyCallback,
    ChannelMoveCallback,
    DisconnectCallback,
    PlaybackOptions,
    SeekOffset,
    TrackEndCallback,
)


class AudioSourceFactory(Protocol):
    """Factory that constructs a Discord ``AudioSource`` for a stream URL."""

    def __call__(
        self,
        stream_url: str,
        /,
        *,
        playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
        seek_seconds: SeekOffset = 0.0,
    ) -> AudioSource: ...


def _default_audio_factory(
    stream_url: str,
    *,
    playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
    seek_seconds: SeekOffset = 0.0,
) -> AudioSource:
    return FFmpegOpusAudio(
        stream_url,
        before_options=DEFAULT_FFMPEG_BEFORE_OPTIONS,
        options=build_ffmpeg_options(playback, seek_seconds=seek_seconds),
    )


class DiscordVoiceManager:
    """Manage Discord voice connections and FFmpeg playback for an injected bot."""

    def __init__(
        self,
        bot: BotClient,
        *,
        audio_factory: AudioSourceFactory | None = None,
    ) -> None:
        self.bot = bot
        self._audio_factory = audio_factory or _default_audio_factory
        self._on_disconnect: DisconnectCallback | None = None
        self._on_channel_move: ChannelMoveCallback | None = None
        self._on_channel_empty_changed: ChannelEmptyCallback | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_guilds: set[int] = set()
        self._logger = logging.getLogger(__name__)
        self.bot.add_listener(self._on_voice_state_update, "on_voice_state_update")

    def _raw_voice_client(self, guild_id: int) -> VoiceClient | None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        vc = guild.voice_client
        return vc if isinstance(vc, VoiceClient) else None

    def _connected_voice_client(self, guild_id: int) -> VoiceClient | None:
        vc = self._raw_voice_client(guild_id)
        if vc is None or not vc.is_connected():
            return None
        return vc

    def connected_channel_id(self, guild_id: int) -> int | None:
        vc = self._connected_voice_client(guild_id)
        return vc.channel.id if vc is not None else None

    def non_bot_member_count(self, guild_id: int) -> int:
        vc = self._connected_voice_client(guild_id)
        if vc is None:
            return 0
        return sum(1 for member in vc.channel.members if not member.bot)

    def is_playing(self, guild_id: int) -> bool:
        vc = self._connected_voice_client(guild_id)
        return vc.is_playing() if vc is not None else False

    def is_paused(self, guild_id: int) -> bool:
        vc = self._connected_voice_client(guild_id)
        return vc.is_paused() if vc is not None else False

    async def connect(self, guild_id: int, channel_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise VoiceError("Could not find this server.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (VoiceChannel, StageChannel)):
            raise VoiceError("Voice channel not found.")

        raw_vc = self._raw_voice_client(guild_id)
        if raw_vc is not None and not raw_vc.is_connected():
            try:
                await raw_vc.disconnect(force=True)
            except (ClientException, HTTPException, TimeoutError):
                self._logger.debug(
                    "Failed to clean up stale voice client in guild %s", guild_id
                )
            raw_vc = None

        if raw_vc is not None:
            self._active_guilds.add(guild_id)
            if raw_vc.channel.id == channel.id:
                return
            try:
                await raw_vc.move_to(channel)
            except (ClientException, Forbidden, TimeoutError) as exc:
                raise VoiceError(f"Could not move to voice channel: {exc}") from exc
        else:
            try:
                await channel.connect(self_deaf=True)
            except (ClientException, Forbidden, TimeoutError) as exc:
                raise VoiceError(f"Could not connect to voice channel: {exc}") from exc
            self._active_guilds.add(guild_id)

        if isinstance(channel, StageChannel):
            try:
                await guild.me.edit(suppress=False)
            except (Forbidden, HTTPException):
                self._logger.debug(
                    "Could not unsuppress in stage channel %s", channel.id
                )

    async def disconnect(self, guild_id: int) -> None:
        self._active_guilds.discard(guild_id)
        vc = self._raw_voice_client(guild_id)
        if vc is not None:
            await vc.disconnect(force=False)

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error("Voice callback failed", exc_info=exc)

    def play(
        self,
        guild_id: int,
        stream_url: str,
        *,
        after: TrackEndCallback,
        playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
        seek_seconds: SeekOffset = 0.0,
    ) -> None:
        vc = self._connected_voice_client(guild_id)
        if vc is None:
            raise VoiceError("I'm not connected to a voice channel.")

        loop = asyncio.get_running_loop()
        source = self._audio_factory(
            stream_url,
            playback=playback,
            seek_seconds=seek_seconds,
        )

        def _threadsafe_after(error: Exception | None) -> None:
            # VoiceClient invokes `after` from its audio thread; hop back to asyncio.
            async def _run() -> None:
                await after(error)

            def _schedule() -> None:
                task: asyncio.Task[None] = loop.create_task(_run())
                self._tasks.add(task)
                task.add_done_callback(self._finish_task)

            try:
                loop.call_soon_threadsafe(_schedule)
            except RuntimeError:
                pass

        try:
            vc.play(source, after=_threadsafe_after)
        except ClientException as exc:
            source.cleanup()
            raise VoiceError(f"Could not start playback: {exc}") from exc
        except Exception:
            source.cleanup()
            raise

    def stop(self, guild_id: int) -> None:
        vc = self._connected_voice_client(guild_id)
        if vc is not None and (vc.is_playing() or vc.is_paused()):
            vc.stop()

    def pause(self, guild_id: int) -> bool:
        vc = self._connected_voice_client(guild_id)
        if vc is None or not vc.is_playing():
            return False
        vc.pause()
        return True

    def resume(self, guild_id: int) -> bool:
        vc = self._connected_voice_client(guild_id)
        if vc is None or not vc.is_paused():
            return False
        vc.resume()
        return True

    async def set_channel_status(
        self,
        guild_id: int,
        status: str | None,
        *,
        channel_id: int | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        target_id = (
            channel_id
            if channel_id is not None
            else self.connected_channel_id(guild_id)
        )
        if target_id is None:
            return
        channel = guild.get_channel(target_id)
        if not isinstance(channel, VoiceChannel):
            return
        trimmed = status[:DISCORD_VOICE_STATUS_MAX_LENGTH] if status else None
        try:
            await channel.edit(status=trimmed)
        except (Forbidden, HTTPException):
            self._logger.debug(
                "Could not update voice status for channel %s", channel.id
            )

    def set_on_disconnect(self, callback: DisconnectCallback | None) -> None:
        self._on_disconnect = callback

    def set_on_channel_move(self, callback: ChannelMoveCallback | None) -> None:
        self._on_channel_move = callback

    def set_on_channel_empty_changed(
        self, callback: ChannelEmptyCallback | None
    ) -> None:
        self._on_channel_empty_changed = callback

    async def _on_voice_state_update(
        self,
        member: Member,
        before: VoiceState,
        after: VoiceState,
    ) -> None:
        if self.bot.user is None:
            return

        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                self._active_guilds.discard(member.guild.id)
                if self._on_disconnect is not None:
                    await self._on_disconnect(member.guild.id)
                return
            if (
                before.channel is not None
                and after.channel is not None
                and before.channel.id != after.channel.id
                and self._on_channel_move is not None
            ):
                await self._on_channel_move(
                    member.guild.id, before.channel.id, after.channel.id
                )
            return

        if member.bot or self._on_channel_empty_changed is None:
            return

        bot_channel_id = self.connected_channel_id(member.guild.id)
        if bot_channel_id is None:
            return

        before_id = before.channel.id if before.channel is not None else None
        after_id = after.channel.id if after.channel is not None else None
        if before_id == after_id:
            return
        if before_id == bot_channel_id or after_id == bot_channel_id:
            is_empty = self.non_bot_member_count(member.guild.id) == 0
            await self._on_channel_empty_changed(member.guild.id, is_empty)

    async def aclose(self) -> None:
        self._on_disconnect = None
        self._on_channel_move = None
        self._on_channel_empty_changed = None
        self.bot.remove_listener(self._on_voice_state_update, "on_voice_state_update")

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

        for guild_id in list(self._active_guilds):
            self._active_guilds.discard(guild_id)
            vc = self._raw_voice_client(guild_id)
            if vc is not None:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                try:
                    await vc.disconnect(force=True)
                except (ClientException, HTTPException, TimeoutError):
                    self._logger.debug(
                        "Failed to disconnect voice client in guild %s", guild_id
                    )

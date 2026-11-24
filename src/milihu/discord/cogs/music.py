"""Discord commands for voice music playback."""

import logging
from typing import Literal, override

from discord import (
    AllowedMentions,
    ButtonStyle,
    Forbidden,
    HTTPException,
    Interaction,
    Member,
    SeparatorSpacing,
    StageChannel,
    VoiceChannel,
    app_commands,
)
from discord.ext import commands
from discord.ui import (
    Button,
    Container,
    LayoutView,
    Section,
    Separator,
    TextDisplay,
)
from discord.utils import escape_markdown

from milihu.discord.cogs._base import BaseCog
from milihu.discord.types.bot import BotClient
from milihu.exceptions.voice import VoiceError
from milihu.faces.music import MusicPlayer
from milihu.types.default.cogs import (
    COLOR_LIVE,
    COLOR_PAUSED,
    COLOR_PLAYING,
)
from milihu.types.default.voice import (
    DEFAULT_MAX_QUEUE_DISPLAY,
    DEFAULT_PLAYBACK_OPTIONS,
)
from milihu.types.voice import (
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    LoopMode,
    PlaybackOptions,
    QueueSnapshot,
    Track,
)


class _NowPlayingView(LayoutView):
    """Interactive Components v2 card for the currently playing track."""

    def __init__(self, player: MusicPlayer, guild_id: int, track: Track) -> None:
        super().__init__(timeout=None)
        self.player = player
        self.guild_id = guild_id

        self._queue_button: Button[_NowPlayingView] = Button(
            style=ButtonStyle.secondary,
            label="Queue",
        )
        self._queue_button.callback = self._on_queue

        heading = "## 🔴 Now Playing • LIVE" if track.is_live else "## 🎵 Now Playing"
        accent = COLOR_LIVE if track.is_live else COLOR_PLAYING
        title = escape_markdown(track.title)

        self.add_item(
            Container[_NowPlayingView](
                Section[_NowPlayingView](
                    TextDisplay[_NowPlayingView](heading),
                    accessory=Button(
                        style=ButtonStyle.link,
                        label="Source",
                        url=str(track.source_url),
                    ),
                ),
                TextDisplay[_NowPlayingView](f"**{title}**"),
                Section[_NowPlayingView](
                    TextDisplay[_NowPlayingView](
                        f"-# Requested by: <@{track.requested_by}>"
                    ),
                    accessory=self._queue_button,
                ),
                accent_colour=accent,
            )
        )

    async def _on_queue(self, interaction: Interaction[BotClient]) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Music controls can only be used in a server.",
                ephemeral=True,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        snapshot = self.player.get_queue(interaction.guild_id)
        await interaction.response.send_message(
            view=MusicCog.build_queue_view(snapshot),
            ephemeral=True,
            allowed_mentions=AllowedMentions.none(),
        )


class MusicCog(BaseCog):
    """Presents hybrid group and prefix commands for music playback."""

    def __init__(self, bot: BotClient, player: MusicPlayer) -> None:
        super().__init__(bot)
        self.player = player
        self._logger = logging.getLogger(__name__)
        self.player.set_on_track_start(self._announce_now_playing)

    @override
    async def cog_unload(self) -> None:
        await self.player.aclose()

    @staticmethod
    def _guild_id(ctx: commands.Context[BotClient]) -> int:
        if ctx.guild is None:
            raise VoiceError("Music commands can only be used in a server.")
        return ctx.guild.id

    @staticmethod
    def _member_voice_channel_id(ctx: commands.Context[BotClient]) -> int | None:
        if (
            isinstance(ctx.author, Member)
            and ctx.author.voice is not None
            and ctx.author.voice.channel is not None
        ):
            return ctx.author.voice.channel.id
        return None

    def build_now_playing_view(self, guild_id: int, track: Track) -> LayoutView:
        return _NowPlayingView(self.player, guild_id, track)

    @staticmethod
    def build_queue_view(snapshot: QueueSnapshot) -> LayoutView:
        status = "Paused" if snapshot.is_paused else "Playing"
        if snapshot.current is not None:
            live_tag = " • LIVE" if snapshot.current.is_live else ""
            current_title = escape_markdown(snapshot.current.title)
            requested_by = snapshot.current.requested_by
            now_line = (
                f"### {current_title}\n"
                f"-# {status}{live_tag} • Requested by: <@{requested_by}>"
            )
        else:
            now_line = "_Nothing is playing right now._"
        small_sep = SeparatorSpacing.small
        items: list[TextDisplay[LayoutView] | Separator[LayoutView]] = [
            TextDisplay[LayoutView]("### 📜 Music Queue"),
            Separator[LayoutView](visible=True, spacing=small_sep),
            TextDisplay[LayoutView](now_line),
        ]
        if snapshot.upcoming:
            lines = [
                f"{idx}. **{escape_markdown(track.title)}**"
                f"{' [LIVE]' if track.is_live else ''} — <@{track.requested_by}>"
                for idx, track in enumerate(
                    snapshot.upcoming[:DEFAULT_MAX_QUEUE_DISPLAY], start=1
                )
            ]
            remaining = len(snapshot.upcoming) - DEFAULT_MAX_QUEUE_DISPLAY
            if remaining > 0:
                lines.append(f"-# ...and {remaining} more")
            items.append(Separator[LayoutView](visible=True, spacing=small_sep))
            items.append(TextDisplay[LayoutView]("\n".join(lines)))

        footer = (
            f"-# Loop: `{snapshot.loop_mode}` • Up next: {len(snapshot.upcoming)}"
        )
        if snapshot.playback != DEFAULT_PLAYBACK_OPTIONS:
            pitch_label = (
                "pitch preserved"
                if snapshot.playback.preserve_pitch
                else "pitch shifted"
            )
            footer = f"{footer} • Speed: `{snapshot.playback.speed:g}x` ({pitch_label})"
        items.append(Separator[LayoutView](visible=True, spacing=small_sep))
        items.append(TextDisplay[LayoutView](footer))
        accent = COLOR_PAUSED if snapshot.is_paused else COLOR_PLAYING
        return LayoutView().add_item(
            Container[LayoutView](*items, accent_colour=accent)
        )

    async def _announce_now_playing(
        self,
        guild_id: int,
        channel_id: int,
        track: Track,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (VoiceChannel, StageChannel)):
            return
        try:
            await channel.send(
                view=self.build_now_playing_view(guild_id, track),
                allowed_mentions=AllowedMentions.none(),
            )
        except (Forbidden, HTTPException):
            self._logger.debug(
                "Could not send Now Playing message to voice channel %s",
                channel_id,
            )

    @commands.hybrid_group(name="music", aliases=["m"])
    @commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def music(self, ctx: commands.Context[BotClient]) -> None:
        """Control voice music playback."""
        if ctx.invoked_subcommand is None:
            await self.queue(ctx)

    @commands.command(
        name="mp", hidden=True, extras={"help_shortcut_for": "music play"}
    )
    @commands.guild_only()
    async def quick_play(self, ctx: commands.Context[BotClient], *, url: str) -> None:
        """Play audio with the short prefix command."""
        await self.play(ctx, url=url)

    @commands.command(
        name="ms", hidden=True, extras={"help_shortcut_for": "music skip"}
    )
    @commands.guild_only()
    async def quick_skip(self, ctx: commands.Context[BotClient]) -> None:
        """Skip the current track with the short prefix command."""
        await self.skip(ctx)

    @commands.command(
        name="mst", hidden=True, extras={"help_shortcut_for": "music stop"}
    )
    @commands.guild_only()
    async def quick_stop(self, ctx: commands.Context[BotClient]) -> None:
        """Stop playback with the short prefix command."""
        await self.stop(ctx)

    @commands.command(
        name="mpa", hidden=True, extras={"help_shortcut_for": "music pause"}
    )
    @commands.guild_only()
    async def quick_pause(self, ctx: commands.Context[BotClient]) -> None:
        """Pause playback with the short prefix command."""
        await self.pause(ctx)

    @commands.command(
        name="mr", hidden=True, extras={"help_shortcut_for": "music resume"}
    )
    @commands.guild_only()
    async def quick_resume(self, ctx: commands.Context[BotClient]) -> None:
        """Resume playback with the short prefix command."""
        await self.resume(ctx)

    @commands.command(
        name="mq", hidden=True, extras={"help_shortcut_for": "music queue"}
    )
    @commands.guild_only()
    async def quick_queue(self, ctx: commands.Context[BotClient]) -> None:
        """Show the queue with the short prefix command."""
        await self.queue(ctx)

    @commands.command(
        name="ml", hidden=True, extras={"help_shortcut_for": "music loop"}
    )
    @commands.guild_only()
    async def quick_loop(
        self,
        ctx: commands.Context[BotClient],
        mode: Literal["off", "track", "queue"],
    ) -> None:
        """Set loop mode with the short prefix command."""
        await self.loop(ctx, mode)

    @commands.command(
        name="mrm", hidden=True, extras={"help_shortcut_for": "music remove"}
    )
    @commands.guild_only()
    async def quick_remove(
        self, ctx: commands.Context[BotClient], position: int
    ) -> None:
        """Remove a queued track with the short prefix command."""
        await self.remove(ctx, position)

    @commands.command(
        name="mc", hidden=True, extras={"help_shortcut_for": "music clear"}
    )
    @commands.guild_only()
    async def quick_clear(self, ctx: commands.Context[BotClient]) -> None:
        """Clear the queue with the short prefix command."""
        await self.clear(ctx)

    @commands.command(
        name="msh", hidden=True, extras={"help_shortcut_for": "music shuffle"}
    )
    @commands.guild_only()
    async def quick_shuffle(self, ctx: commands.Context[BotClient]) -> None:
        """Shuffle the queue with the short prefix command."""
        await self.shuffle(ctx)

    @commands.command(
        name="msp", hidden=True, extras={"help_shortcut_for": "music speed"}
    )
    @commands.guild_only()
    async def quick_speed(
        self,
        ctx: commands.Context[BotClient],
        speed: commands.Range[float, MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED],
    ) -> None:
        """Set playback speed with the short prefix command."""
        await self.speed(ctx, speed=speed)

    @commands.command(
        name="mpp", hidden=True, extras={"help_shortcut_for": "music pitch"}
    )
    @commands.guild_only()
    async def quick_pitch(
        self,
        ctx: commands.Context[BotClient],
        preserve: bool | None = None,
    ) -> None:
        """Toggle or set pitch preservation with the short prefix command."""
        await self.pitch(ctx, preserve=preserve)

    @music.command(name="play", aliases=["p"], extras={"help_order": 1})
    @app_commands.describe(
        url="Search query or music/playlist URL (Spotify, YouTube, SoundCloud, etc.)."
    )
    async def play(self, ctx: commands.Context[BotClient], *, url: str) -> None:
        """Play or enqueue audio from a URL or search query."""
        guild_id = self._guild_id(ctx)
        channel_id = self._member_voice_channel_id(ctx)
        in_voice_chat = channel_id is not None and ctx.channel.id == channel_id
        async with ctx.typing(ephemeral=in_voice_chat):
            result = await self.player.enqueue(
                guild_id,
                channel_id,
                url,
                requested_by=ctx.author.id,
            )
        title = escape_markdown(result.track.title)
        from_playlist = (
            f" from **{escape_markdown(result.playlist_title)}**"
            if result.playlist_title is not None
            else ""
        )
        warning_suffix = f"\n-# {result.warning}" if result.warning is not None else ""
        if result.started_immediately:
            if result.enqueued_count > 1:
                extra = result.enqueued_count - 1
                message = (
                    f"Playing **{title}** and queued {extra} more track(s)"
                    f"{from_playlist}."
                )
            else:
                message = f"Playing **{title}**.{warning_suffix}"
            await ctx.reply(
                message,
                ephemeral=in_voice_chat,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        if result.enqueued_count > 1:
            message = (
                f"Queued {result.enqueued_count} tracks{from_playlist} "
                f"starting at position #{result.position}."
            )
        else:
            message = (
                f"Queued **{title}** at position #{result.position}.{warning_suffix}"
            )
        await ctx.reply(
            message,
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="skip", aliases=["s"], extras={"help_order": 4})
    async def skip(self, ctx: commands.Context[BotClient]) -> None:
        """Skip the currently playing track."""
        skipped = await self.player.skip(
            self._guild_id(ctx), self._member_voice_channel_id(ctx)
        )
        await ctx.reply(
            f"Skipped **{escape_markdown(skipped.title)}**.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="pause", extras={"help_order": 2})
    async def pause(self, ctx: commands.Context[BotClient]) -> None:
        """Pause the currently playing track."""
        track = await self.player.pause(
            self._guild_id(ctx), self._member_voice_channel_id(ctx)
        )
        await ctx.reply(
            f"Paused **{escape_markdown(track.title)}**.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="resume", extras={"help_order": 3})
    async def resume(self, ctx: commands.Context[BotClient]) -> None:
        """Resume paused playback."""
        track = await self.player.resume(
            self._guild_id(ctx), self._member_voice_channel_id(ctx)
        )
        await ctx.reply(
            f"Resumed **{escape_markdown(track.title)}**.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="stop", aliases=["leave"], extras={"help_order": 5})
    async def stop(self, ctx: commands.Context[BotClient]) -> None:
        """Stop playback, clear the queue, and disconnect."""
        await self.player.stop(self._guild_id(ctx), self._member_voice_channel_id(ctx))
        await ctx.reply(
            "Stopped playback and disconnected.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="queue", aliases=["q"], extras={"help_order": 6})
    async def queue(self, ctx: commands.Context[BotClient]) -> None:
        """Show the current track and upcoming queue."""
        snapshot = self.player.get_queue(self._guild_id(ctx))
        await ctx.reply(
            view=self.build_queue_view(snapshot),
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="loop", extras={"help_order": 10})
    @app_commands.describe(mode="Loop behavior for playback.")
    async def loop(
        self,
        ctx: commands.Context[BotClient],
        mode: Literal["off", "track", "queue"],
    ) -> None:
        """Set loop mode (`off`, `track`, or `queue`)."""
        selected = await self.player.set_loop(
            self._guild_id(ctx),
            self._member_voice_channel_id(ctx),
            LoopMode(mode),
        )
        await ctx.reply(
            f"Loop mode set to `{selected}`.",
            allowed_mentions=AllowedMentions.none(),
        )

    @staticmethod
    def _format_playback_summary(playback: PlaybackOptions) -> str:
        pitch_state = "on" if playback.preserve_pitch else "off"
        return f"`{playback.speed:g}x` (preserve pitch: `{pitch_state}`)"

    @music.command(name="speed", aliases=["sp"], extras={"help_order": 11})
    @app_commands.describe(
        speed=(
            f"Playback speed multiplier ({MIN_PLAYBACK_SPEED:g} to "
            f"{MAX_PLAYBACK_SPEED:g}, 1.0 = normal)."
        ),
        preserve_pitch=(
            "Keep the original pitch when changing speed "
            "(keeps current setting if omitted)."
        ),
    )
    async def speed(
        self,
        ctx: commands.Context[BotClient],
        speed: commands.Range[float, MIN_PLAYBACK_SPEED, MAX_PLAYBACK_SPEED],
        preserve_pitch: bool | None = None,
    ) -> None:
        """Set playback speed and optionally pitch preservation."""
        guild_id = self._guild_id(ctx)
        async with ctx.typing():
            updated = await self.player.set_speed(
                guild_id,
                self._member_voice_channel_id(ctx),
                speed=speed,
                preserve_pitch=preserve_pitch,
            )
        await ctx.reply(
            f"Playback speed set to {self._format_playback_summary(updated)}.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="pitch", aliases=["pp"], extras={"help_order": 12})
    @app_commands.describe(
        preserve="Set pitch preservation on or off (leave blank to toggle).",
    )
    async def pitch(
        self,
        ctx: commands.Context[BotClient],
        preserve: bool | None = None,
    ) -> None:
        """Toggle or set pitch preservation for playback speed."""
        guild_id = self._guild_id(ctx)
        target = (
            not self.player.get_queue(guild_id).playback.preserve_pitch
            if preserve is None
            else preserve
        )
        async with ctx.typing():
            updated = await self.player.set_speed(
                guild_id,
                self._member_voice_channel_id(ctx),
                preserve_pitch=target,
            )
        pitch_state = "on" if updated.preserve_pitch else "off"
        await ctx.reply(
            f"Preserve pitch set to `{pitch_state}` (speed: `{updated.speed:g}x`).",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="remove", aliases=["rm"], extras={"help_order": 7})
    @app_commands.describe(position="1-based position in the upcoming queue.")
    async def remove(self, ctx: commands.Context[BotClient], position: int) -> None:
        """Remove a track from the upcoming queue."""
        removed = await self.player.remove(
            self._guild_id(ctx),
            self._member_voice_channel_id(ctx),
            position,
        )
        await ctx.reply(
            f"Removed **{escape_markdown(removed.title)}** from the queue.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="clear", extras={"help_order": 8})
    async def clear(self, ctx: commands.Context[BotClient]) -> None:
        """Clear all upcoming tracks from the queue."""
        count = await self.player.clear(
            self._guild_id(ctx), self._member_voice_channel_id(ctx)
        )
        await ctx.reply(
            f"Cleared {count} track(s) from the queue.",
            allowed_mentions=AllowedMentions.none(),
        )

    @music.command(name="shuffle", extras={"help_order": 9})
    async def shuffle(self, ctx: commands.Context[BotClient]) -> None:
        """Shuffle the upcoming queue."""
        count = await self.player.shuffle(
            self._guild_id(ctx), self._member_voice_channel_id(ctx)
        )
        await ctx.reply(
            f"Shuffled {count} track(s) in the queue.",
            allowed_mentions=AllowedMentions.none(),
        )

    @override
    async def cog_command_error(
        self,
        ctx: commands.Context[BotClient],
        error: Exception,
    ) -> None:
        original = self._unwrap_command_error(error)
        if isinstance(original, VoiceError):
            await ctx.reply(
                str(original),
                ephemeral=True,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        if isinstance(original, commands.NoPrivateMessage):
            await ctx.reply(
                "Music commands can only be used in a server.",
                ephemeral=True,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        if isinstance(
            original,
            (
                commands.BadArgument,
                commands.BadLiteralArgument,
                commands.MissingRequiredArgument,
            ),
        ):
            await ctx.reply(
                str(original),
                ephemeral=True,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        await super().cog_command_error(ctx, error)

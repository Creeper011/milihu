"""Multi-guild music queue and playback orchestration service."""

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field

from pydantic import HttpUrl, ValidationError

from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.types.default.voice import (
    DEFAULT_ICY_POLL_SECONDS,
    DEFAULT_PLAYBACK_OPTIONS,
    DEFAULT_VOICE_IDLE_TIMEOUT_SECONDS,
)
from milihu.types.playlist import ResolvedPlaylist, ResolvedStream
from milihu.types.voice import (
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    EnqueueResult,
    LoopMode,
    PlaybackOptions,
    PlayRequest,
    QueueSnapshot,
    SeekOffset,
    Track,
    TrackStartCallback,
)
from milihu.usecases.faces.media import MediaBackend
from milihu.usecases.faces.playlist import PlaylistResolver
from milihu.usecases.faces.search import SearchResolver
from milihu.usecases.faces.voice import StreamProber, VoiceManager
from milihu.usecases.track_resolver import TrackResolver

FAST_FAILURE_THRESHOLD_SECONDS = 2.0
MAX_CONSECUTIVE_FAST_FAILURES = 3


@dataclass(slots=True)
class _QueuedTrack:
    track: Track
    stream_url: str | None = None
    is_direct: bool = False


@dataclass(slots=True)
class _GuildQueue:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current: _QueuedTrack | None = None
    upcoming: deque[_QueuedTrack] = field(default_factory=deque)
    loop_mode: LoopMode = LoopMode.OFF
    playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS
    playback_offset: float = 0.0
    segment_started_at: float | None = None
    pending_speed_restart: bool = False
    idle_task: asyncio.Task[None] | None = None
    icy_task: asyncio.Task[None] | None = None
    status_channel_id: int | None = None
    generation: int = 0
    started_at: float | None = None
    consecutive_fast_failures: int = 0


class MusicPlayerService:
    """Manage per-guild music queues, stream resolution, and voice lifecycle."""

    def __init__(
        self,
        voice: VoiceManager,
        media_backend: MediaBackend | None,
        *,
        stream_probe: StreamProber,
        playlist_resolver: PlaylistResolver | None = None,
        search_resolver: SearchResolver | None = None,
        idle_timeout: float = DEFAULT_VOICE_IDLE_TIMEOUT_SECONDS,
        icy_poll_interval: float = DEFAULT_ICY_POLL_SECONDS,
        on_track_start: TrackStartCallback | None = None,
    ) -> None:
        self.voice = voice
        self.media_backend = media_backend
        self.stream_probe = stream_probe
        self.playlist_resolver = playlist_resolver
        self.search_resolver = search_resolver
        self.resolver = TrackResolver(
            media_backend,
            self.stream_probe,
            playlist_resolver,
            max_backend_failures=MAX_CONSECUTIVE_FAST_FAILURES,
        )
        self.idle_timeout = idle_timeout
        self.icy_poll_interval = icy_poll_interval
        self._on_track_start = on_track_start
        self._queues: dict[int, _GuildQueue] = {}
        self._logger = logging.getLogger(__name__)
        self.voice.set_on_disconnect(self._handle_external_disconnect)
        self.voice.set_on_channel_move(self._handle_channel_move)
        self.voice.set_on_channel_empty_changed(self._handle_channel_empty_changed)

    def set_on_track_start(self, callback: TrackStartCallback | None) -> None:
        self._on_track_start = callback

    def _get_state(self, guild_id: int) -> _GuildQueue:
        return self._queues.setdefault(guild_id, _GuildQueue())

    def _log_task_error(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error("Music background task failed", exc_info=exc)

    @staticmethod
    def _cancel_idle(state: _GuildQueue) -> None:
        if state.idle_task is not None:
            state.idle_task.cancel()
            state.idle_task = None

    @staticmethod
    def _cancel_icy(state: _GuildQueue) -> None:
        if state.icy_task is not None:
            state.icy_task.cancel()
            state.icy_task = None

    @staticmethod
    def _take_status_channel_id(state: _GuildQueue) -> int | None:
        channel_id = state.status_channel_id
        state.status_channel_id = None
        return channel_id

    async def _clear_status_channel(
        self, guild_id: int, channel_id: int | None
    ) -> None:
        if channel_id is not None:
            await self.voice.set_channel_status(guild_id, None, channel_id=channel_id)

    @classmethod
    def _reset_state(cls, state: _GuildQueue, *, cancel_idle: bool = True) -> None:
        state.generation += 1
        if cancel_idle:
            cls._cancel_idle(state)
        cls._cancel_icy(state)
        state.current = None
        state.upcoming.clear()
        state.loop_mode = LoopMode.OFF
        state.playback = DEFAULT_PLAYBACK_OPTIONS
        state.playback_offset = 0.0
        state.segment_started_at = None
        state.pending_speed_restart = False
        state.started_at = None
        state.consecutive_fast_failures = 0

    @staticmethod
    def _clear_playback_timing(state: _GuildQueue) -> None:
        state.playback_offset = 0.0
        state.segment_started_at = None
        state.pending_speed_restart = False

    @staticmethod
    def _current_track(state: _GuildQueue) -> _QueuedTrack | None:
        return state.current

    @staticmethod
    def _current_position_seconds(state: _GuildQueue) -> float:
        if state.segment_started_at is None:
            return state.playback_offset
        elapsed = max(0.0, time.monotonic() - state.segment_started_at)
        return state.playback_offset + elapsed * state.playback.speed

    @staticmethod
    def _validate_playback_options(
        current: PlaybackOptions,
        *,
        speed: float | None,
        preserve_pitch: bool | None,
    ) -> PlaybackOptions:
        next_speed = current.speed if speed is None else speed
        next_preserve_pitch = (
            current.preserve_pitch if preserve_pitch is None else preserve_pitch
        )
        try:
            return PlaybackOptions(
                speed=next_speed,
                preserve_pitch=next_preserve_pitch,
            )
        except ValidationError as exc:
            raise VoiceError(
                f"Speed must be between {MIN_PLAYBACK_SPEED:g}x "
                f"and {MAX_PLAYBACK_SPEED:g}x."
            ) from exc

    def _maybe_evict_guild(self, guild_id: int, state: _GuildQueue) -> None:
        if (
            self._queues.get(guild_id) is state
            and state.current is None
            and not state.upcoming
            and state.idle_task is None
            and state.icy_task is None
            and state.status_channel_id is None
            and self.voice.connected_channel_id(guild_id) is None
        ):
            self._queues.pop(guild_id, None)

    def _ensure_can_enqueue(self, guild_id: int, channel_id: int | None) -> int:
        if channel_id is None:
            raise VoiceError("You must be in a voice channel first.")
        current_channel = self.voice.connected_channel_id(guild_id)
        if current_channel is not None and current_channel != channel_id:
            is_idle = not self.voice.is_playing(guild_id) and not self.voice.is_paused(
                guild_id
            )
            is_empty = self.voice.non_bot_member_count(guild_id) == 0
            if not (is_idle or is_empty):
                raise VoiceError("I'm already playing in another voice channel.")
        return channel_id

    def _require_same_channel(self, guild_id: int, channel_id: int | None) -> None:
        bot_channel = self.voice.connected_channel_id(guild_id)
        if bot_channel is None:
            raise VoiceError("I'm not connected to a voice channel.")
        if channel_id != bot_channel:
            raise VoiceError("You must be in the same voice channel as me.")

    async def _notify_track_start(
        self,
        guild_id: int,
        channel_id: int | None,
        track: Track,
    ) -> None:
        if self._on_track_start is not None and channel_id is not None:
            try:
                await self._on_track_start(guild_id, channel_id, track)
            except Exception:
                self._logger.exception("Failed to notify track start")

    def _start_icy_watcher(
        self,
        guild_id: int,
        state: _GuildQueue,
        stream_url: str,
        generation: int,
    ) -> None:
        self._cancel_icy(state)

        async def _watch() -> None:
            last_title: str | None = None
            last_channel_id: int | None = None
            try:
                while True:
                    if self.voice.is_paused(guild_id):
                        await asyncio.sleep(self.icy_poll_interval)
                        continue
                    metadata = await self.stream_probe.fetch_icy_title(stream_url)
                    if not metadata.supported:
                        return
                    title = metadata.title
                    status_update: tuple[int, str] | None = None
                    async with state.lock:
                        if state.generation != generation:
                            return
                        channel_id = self.voice.connected_channel_id(guild_id)
                        if channel_id is None:
                            return
                        if title and (
                            title != last_title or channel_id != last_channel_id
                        ):
                            last_title = title
                            last_channel_id = channel_id
                            state.status_channel_id = channel_id
                            status_update = (channel_id, title)
                    if status_update is not None:
                        await self.voice.set_channel_status(
                            guild_id, status_update[1], channel_id=status_update[0]
                        )
                    await asyncio.sleep(self.icy_poll_interval)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(_watch())
        task.add_done_callback(self._log_task_error)
        state.icy_task = task

    async def _commit_playback(
        self,
        guild_id: int,
        state: _GuildQueue,
        track: Track,
        stream_url: str,
        *,
        is_direct: bool,
        expected_generation: int,
        schedule_idle_on_error: bool = False,
        seek_seconds: SeekOffset = 0.0,
    ) -> tuple[bool, int | None, int | None]:
        async with state.lock:
            if (
                state.generation != expected_generation
                or self.voice.connected_channel_id(guild_id) is None
            ):
                return False, None, None

            self._cancel_icy(state)
            old_status_channel = self._take_status_channel_id(state)
            next_generation = state.generation + 1
            effective_playback = (
                DEFAULT_PLAYBACK_OPTIONS if track.is_live else state.playback
            )
            effective_seek = 0.0 if track.is_live else seek_seconds
            now = time.monotonic()
            state.current = _QueuedTrack(
                track=track, stream_url=None, is_direct=is_direct
            )
            state.started_at = now
            state.playback_offset = effective_seek
            state.segment_started_at = now
            state.pending_speed_restart = False
            try:
                self.voice.play(
                    guild_id,
                    stream_url,
                    after=lambda err: self._on_track_finished(
                        guild_id, next_generation, err
                    ),
                    playback=effective_playback,
                    seek_seconds=effective_seek,
                )
            except Exception:
                state.current = None
                state.started_at = None
                state.playback_offset = 0.0
                state.segment_started_at = None
                state.pending_speed_restart = False
                if old_status_channel is not None:
                    state.status_channel_id = old_status_channel
                if (
                    schedule_idle_on_error
                    and not state.upcoming
                    and self.voice.connected_channel_id(guild_id) is not None
                ):
                    self._schedule_idle_disconnect(guild_id, state)
                raise

            state.generation = next_generation
            if track.is_live:
                self._start_icy_watcher(guild_id, state, stream_url, next_generation)
            if self.voice.non_bot_member_count(guild_id) == 0:
                self._schedule_idle_disconnect(guild_id, state)
            return True, old_status_channel, self.voice.connected_channel_id(guild_id)

    def _schedule_idle_disconnect(self, guild_id: int, state: _GuildQueue) -> None:
        self._cancel_idle(state)

        async def _idle_wait() -> None:
            try:
                await asyncio.sleep(self.idle_timeout)
            except asyncio.CancelledError:
                return
            old_status_channel: int | None = None
            should_disconnect = False
            async with state.lock:
                if state.idle_task is not asyncio.current_task():
                    return
                state.idle_task = None
                if self.voice.connected_channel_id(guild_id) is None:
                    self._maybe_evict_guild(guild_id, state)
                    return
                is_idle = state.current is None and not state.upcoming
                is_empty = self.voice.non_bot_member_count(guild_id) == 0
                if is_idle or is_empty:
                    old_status_channel = self._take_status_channel_id(state)
                    self._reset_state(state, cancel_idle=False)
                    self.voice.stop(guild_id)
                    should_disconnect = True
            if should_disconnect:
                await self._clear_status_channel(guild_id, old_status_channel)
                await self.voice.disconnect(guild_id)
                async with state.lock:
                    self._maybe_evict_guild(guild_id, state)

        task = asyncio.create_task(_idle_wait())
        task.add_done_callback(self._log_task_error)
        state.idle_task = task

    async def _play_next_available(
        self,
        guild_id: int,
        state: _GuildQueue,
        expected_generation: int,
        first_candidate: _QueuedTrack | None = None,
    ) -> None:
        candidate = first_candidate
        status_to_clear: int | None = None
        backend_failures = 0
        while True:
            async with state.lock:
                if (
                    state.generation != expected_generation
                    or self.voice.connected_channel_id(guild_id) is None
                ):
                    return
                if candidate is None:
                    if not state.upcoming:
                        self._cancel_icy(state)
                        status_to_clear = self._take_status_channel_id(state)
                        state.current = None
                        state.started_at = None
                        self._clear_playback_timing(state)
                        self._schedule_idle_disconnect(guild_id, state)
                        break
                    candidate = state.upcoming.popleft()
                state.current = _QueuedTrack(
                    track=candidate.track,
                    stream_url=None,
                    is_direct=candidate.is_direct,
                )
                state.started_at = None

            try:
                stream_url, is_direct = await self.resolver.prepare_stream(
                    candidate.track.source_url,
                    candidate.stream_url,
                    is_direct=candidate.is_direct,
                )
                started, old_status_channel, channel_id = await self._commit_playback(
                    guild_id,
                    state,
                    candidate.track,
                    stream_url,
                    is_direct=is_direct,
                    expected_generation=expected_generation,
                )
            except (BackendUnavailableError, OSError, RuntimeError) as exc:
                backend_failures += 1
                self._logger.warning(
                    "Skipping track %s after backend error: %s",
                    candidate.track.source_url,
                    exc,
                )
                if backend_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
                    self._logger.warning(
                        "Stopping queue advance in guild %s after %d backend failures",
                        guild_id,
                        backend_failures,
                    )
                    async with state.lock:
                        if state.generation == expected_generation:
                            self._cancel_icy(state)
                            status_to_clear = self._take_status_channel_id(state)
                            state.current = None
                            state.started_at = None
                            self._schedule_idle_disconnect(guild_id, state)
                    break
                candidate = None
                continue
            except VoiceError as exc:
                backend_failures = 0
                self._logger.warning(
                    "Skipping unplayable track %s: %s",
                    candidate.track.source_url,
                    exc,
                )
                candidate = None
                continue

            if not started:
                return
            await self._clear_status_channel(guild_id, old_status_channel)
            await self._notify_track_start(guild_id, channel_id, candidate.track)
            return

        await self._clear_status_channel(guild_id, status_to_clear)

    async def _on_track_finished(
        self,
        guild_id: int,
        generation: int,
        error: Exception | None,
    ) -> None:
        if error is not None:
            self._logger.warning("Playback error in guild %s: %s", guild_id, error)

        state = self._queues.get(guild_id)
        if state is None:
            return

        status_to_clear: int | None = None
        next_candidate: _QueuedTrack | None = None
        expected_gen: int | None = None

        async with state.lock:
            if state.generation != generation:
                return
            if self.voice.connected_channel_id(guild_id) is None:
                status_to_clear = self._take_status_channel_id(state)
                self._reset_state(state)
                self._maybe_evict_guild(guild_id, state)
            else:
                elapsed = (
                    time.monotonic() - state.started_at
                    if state.started_at is not None
                    else FAST_FAILURE_THRESHOLD_SECONDS
                )
                state.started_at = None
                self._clear_playback_timing(state)
                if error is not None or elapsed < FAST_FAILURE_THRESHOLD_SECONDS:
                    state.consecutive_fast_failures += 1
                else:
                    state.consecutive_fast_failures = 0

                if state.consecutive_fast_failures >= MAX_CONSECUTIVE_FAST_FAILURES:
                    self._logger.warning(
                        "Stopping playback in guild %s after %d fast failures",
                        guild_id,
                        state.consecutive_fast_failures,
                    )
                    self._cancel_icy(state)
                    status_to_clear = self._take_status_channel_id(state)
                    state.current = None
                    state.consecutive_fast_failures = 0
                    if state.loop_mode is LoopMode.TRACK:
                        state.loop_mode = LoopMode.OFF
                    self._schedule_idle_disconnect(guild_id, state)
                else:
                    finished = state.current
                    state.current = None
                    state.generation += 1
                    expected_gen = state.generation
                    if state.loop_mode is LoopMode.TRACK and finished is not None:
                        next_candidate = _QueuedTrack(
                            track=finished.track,
                            stream_url=None,
                            is_direct=finished.is_direct,
                        )
                    elif state.loop_mode is LoopMode.QUEUE and finished is not None:
                        state.upcoming.append(
                            _QueuedTrack(
                                track=finished.track,
                                stream_url=None,
                                is_direct=finished.is_direct,
                            )
                        )

        await self._clear_status_channel(guild_id, status_to_clear)
        if expected_gen is not None:
            await self._play_next_available(
                guild_id, state, expected_gen, first_candidate=next_candidate
            )

    async def _handle_external_disconnect(self, guild_id: int) -> None:
        state = self._queues.get(guild_id)
        if state is None:
            return
        async with state.lock:
            old_status_channel = self._take_status_channel_id(state)
            self._reset_state(state)
            self._maybe_evict_guild(guild_id, state)
        await self._clear_status_channel(guild_id, old_status_channel)

    async def _handle_channel_move(
        self,
        guild_id: int,
        old_channel_id: int,
        new_channel_id: int,
    ) -> None:
        state = self._queues.get(guild_id)
        if state is None:
            return
        status_to_clear: int | None = None
        async with state.lock:
            if state.status_channel_id == old_channel_id:
                status_to_clear = old_channel_id
                state.status_channel_id = None
            if self.voice.connected_channel_id(guild_id) is not None:
                if self.voice.non_bot_member_count(guild_id) == 0:
                    if state.idle_task is None:
                        self._schedule_idle_disconnect(guild_id, state)
                elif state.current is not None:
                    self._cancel_idle(state)
        await self._clear_status_channel(guild_id, status_to_clear)

    async def _handle_channel_empty_changed(
        self,
        guild_id: int,
        is_empty: bool,
    ) -> None:
        state = self._queues.get(guild_id)
        if state is None:
            if not is_empty or self.voice.connected_channel_id(guild_id) is None:
                return
            state = self._get_state(guild_id)
        async with state.lock:
            if self.voice.connected_channel_id(guild_id) is None:
                return
            if is_empty:
                if state.idle_task is None:
                    self._schedule_idle_disconnect(guild_id, state)
            elif state.current is not None:
                self._cancel_idle(state)

    async def _ensure_connected(
        self,
        guild_id: int,
        state: _GuildQueue,
        channel_id: int | None,
    ) -> int:
        async with state.connect_lock:
            old_status_channel: int | None = None
            needs_connect = False
            async with state.lock:
                target_channel = self._ensure_can_enqueue(guild_id, channel_id)
                current_channel = self.voice.connected_channel_id(guild_id)
                if current_channel != target_channel:
                    needs_connect = True
                    old_status_channel = self._take_status_channel_id(state)
                    if current_channel is not None:
                        # Clean takeover from an empty/idle channel.
                        self._reset_state(state)
                        self.voice.stop(guild_id)
            await self._clear_status_channel(guild_id, old_status_channel)
            if needs_connect:
                await self.voice.connect(guild_id, target_channel)
            return target_channel

    async def _start_resolved_track(
        self,
        guild_id: int,
        state: _GuildQueue,
        track: Track,
        stream_url: str,
        *,
        is_direct: bool,
        expected_gen: int,
    ) -> None:
        if is_direct:
            try:
                await self.stream_probe.validate_url(stream_url)
            except Exception:
                async with state.lock:
                    if state.generation == expected_gen:
                        state.current = None
                        if (
                            not state.upcoming
                            and self.voice.connected_channel_id(guild_id) is not None
                        ):
                            self._schedule_idle_disconnect(guild_id, state)
                raise

        started, old_status_channel, connected_channel = await self._commit_playback(
            guild_id,
            state,
            track,
            stream_url,
            is_direct=is_direct,
            expected_generation=expected_gen,
            schedule_idle_on_error=True,
        )
        if started:
            await self._clear_status_channel(guild_id, old_status_channel)
            await self._notify_track_start(guild_id, connected_channel, track)

    async def _enqueue_playlist(
        self,
        guild_id: int,
        channel_id: int | None,
        playlist: ResolvedPlaylist,
        *,
        requested_by: int,
    ) -> EnqueueResult:
        if not playlist.entries:
            raise VoiceError("Could not load tracks from this playlist.")
        tracks = [
            Track(
                source_url=entry.source_url,
                title=entry.title,
                requested_by=requested_by,
                is_live=False,
            )
            for entry in playlist.entries
        ]

        state = self._get_state(guild_id)
        already_playing_here = (
            self.voice.connected_channel_id(guild_id) == channel_id
            and state.current is not None
        )
        initial_stream: ResolvedStream | None = None
        if not already_playing_here:
            tracks, initial_stream = await self.resolver.resolve_first_playable(tracks)

        await self._ensure_connected(guild_id, state, channel_id)

        started_immediately = False
        position = 0
        expected_gen = 0
        initial_is_direct = (
            initial_stream.is_direct if initial_stream is not None else False
        )
        async with state.lock:
            target_channel = self._ensure_can_enqueue(guild_id, channel_id)
            if self.voice.connected_channel_id(guild_id) != target_channel:
                raise VoiceError("Could not stay connected to your voice channel.")
            self._cancel_idle(state)
            if state.current is None:
                state.consecutive_fast_failures = 0
                state.current = _QueuedTrack(
                    track=tracks[0], stream_url=None, is_direct=initial_is_direct
                )
                state.upcoming.extend(
                    _QueuedTrack(track=t, stream_url=None, is_direct=False)
                    for t in tracks[1:]
                )
                expected_gen = state.generation
                started_immediately = True
            else:
                position = len(state.upcoming) + 1
                state.upcoming.extend(
                    _QueuedTrack(track=t, stream_url=None, is_direct=False)
                    for t in tracks
                )

        if not started_immediately:
            return EnqueueResult(
                track=tracks[0],
                position=position,
                started_immediately=False,
                enqueued_count=len(tracks),
                playlist_title=playlist.title,
            )

        if initial_stream is not None:
            await self._start_resolved_track(
                guild_id,
                state,
                tracks[0],
                initial_stream.stream_url,
                is_direct=initial_stream.is_direct,
                expected_gen=expected_gen,
            )
        else:
            await self._play_next_available(
                guild_id,
                state,
                expected_gen,
                first_candidate=_QueuedTrack(track=tracks[0]),
            )
        return EnqueueResult(
            track=tracks[0],
            position=0,
            started_immediately=True,
            enqueued_count=len(tracks),
            playlist_title=playlist.title,
        )

    async def enqueue(
        self,
        guild_id: int,
        channel_id: int | None,
        url: PlayRequest | str,
        *,
        requested_by: int,
    ) -> EnqueueResult:
        self._ensure_can_enqueue(guild_id, channel_id)
        source_url: HttpUrl
        playlist: ResolvedPlaylist | None = None
        stream: ResolvedStream | None = None
        warning: str | None = None
        if isinstance(url, str):
            value = url.strip()
            if not value:
                raise VoiceError("Enter a URL or search query.")
            try:
                request = PlayRequest.model_validate({"url": value})
            except ValidationError as exc:
                if "://" in value or value.lower().startswith(("http:", "https:")):
                    raise VoiceError("Use a valid HTTP(S) URL.") from exc
                if self.search_resolver is None:
                    raise VoiceError("Search is unavailable.") from exc
                candidates = await self.search_resolver.search(value)
                source_url, stream = await self.resolver.resolve_first_audio(candidates)
            else:
                source_url = request.url
                playlist, stream, warning = await self.resolver.resolve_input(
                    source_url
                )
        else:
            source_url = url.url
            playlist, stream, warning = await self.resolver.resolve_input(source_url)

        if playlist is not None:
            return await self._enqueue_playlist(
                guild_id, channel_id, playlist, requested_by=requested_by
            )
        assert stream is not None

        track = Track(
            source_url=source_url,
            title=stream.title,
            requested_by=requested_by,
            is_live=stream.is_live,
        )

        state = self._get_state(guild_id)
        await self._ensure_connected(guild_id, state, channel_id)

        started_immediately = False
        position = 0
        expected_gen = 0
        async with state.lock:
            target_channel = self._ensure_can_enqueue(guild_id, channel_id)
            if self.voice.connected_channel_id(guild_id) != target_channel:
                raise VoiceError("Could not stay connected to your voice channel.")
            self._cancel_idle(state)
            if state.current is None:
                state.consecutive_fast_failures = 0
                state.current = _QueuedTrack(
                    track=track, stream_url=None, is_direct=stream.is_direct
                )
                expected_gen = state.generation
                started_immediately = True
            else:
                # Drop the initial stream URL so queued tracks re-resolve fresh on play.
                state.upcoming.append(
                    _QueuedTrack(
                        track=track, stream_url=None, is_direct=stream.is_direct
                    )
                )
                position = len(state.upcoming)

        if not started_immediately:
            return EnqueueResult(
                track=track,
                position=position,
                started_immediately=False,
                warning=warning,
            )

        await self._start_resolved_track(
            guild_id,
            state,
            track,
            stream.stream_url,
            is_direct=stream.is_direct,
            expected_gen=expected_gen,
        )
        return EnqueueResult(
            track=track,
            position=0,
            started_immediately=True,
            warning=warning,
        )

    async def skip(self, guild_id: int, channel_id: int | None) -> Track:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            if state.current is None:
                raise VoiceError("Nothing is playing right now.")

            skipped = state.current.track
            skipped_is_direct = state.current.is_direct
            state.generation += 1
            expected_gen = state.generation
            state.consecutive_fast_failures = 0
            state.started_at = None
            state.current = None
            self._clear_playback_timing(state)
            self.voice.stop(guild_id)

            if state.loop_mode is LoopMode.QUEUE:
                state.upcoming.append(
                    _QueuedTrack(
                        track=skipped,
                        stream_url=None,
                        is_direct=skipped_is_direct,
                    )
                )

        await self._play_next_available(guild_id, state, expected_gen)
        return skipped

    async def pause(self, guild_id: int, channel_id: int | None) -> Track:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            if state.current is None:
                raise VoiceError("Nothing is playing right now.")
            if not self.voice.pause(guild_id):
                raise VoiceError("Playback is already paused.")
            state.playback_offset = self._current_position_seconds(state)
            state.segment_started_at = None
            return state.current.track

    async def resume(self, guild_id: int, channel_id: int | None) -> Track:
        state = self._get_state(guild_id)
        restart_track: Track | None = None
        restart_is_direct = False
        expected_gen = 0

        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            if state.current is None:
                raise VoiceError("Nothing is paused right now.")
            if not state.pending_speed_restart:
                if not self.voice.resume(guild_id):
                    raise VoiceError("Playback is not paused.")
                state.segment_started_at = time.monotonic()
                return state.current.track
            if not self.voice.is_paused(guild_id):
                raise VoiceError("Playback is not paused.")
            restart_track = state.current.track
            restart_is_direct = state.current.is_direct
            expected_gen = state.generation

        stream_url, resolved_is_direct = await self.resolver.prepare_stream(
            restart_track.source_url,
            None,
            is_direct=restart_is_direct,
        )

        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            current = self._current_track(state)
            if state.generation != expected_gen or current is None:
                raise VoiceError("Nothing is paused right now.")
            if not self.voice.is_paused(guild_id):
                raise VoiceError("Playback is not paused.")
            seek_seconds = 0.0 if restart_track.is_live else state.playback_offset
            effective_playback = (
                DEFAULT_PLAYBACK_OPTIONS if restart_track.is_live else state.playback
            )
            next_generation = state.generation + 1
            state.generation = next_generation
            self.voice.stop(guild_id)
            now = time.monotonic()
            try:
                self.voice.play(
                    guild_id,
                    stream_url,
                    after=lambda err: self._on_track_finished(
                        guild_id, next_generation, err
                    ),
                    playback=effective_playback,
                    seek_seconds=seek_seconds,
                )
            except Exception:
                state.current = None
                state.started_at = None
                self._clear_playback_timing(state)
                if (
                    not state.upcoming
                    and self.voice.connected_channel_id(guild_id) is not None
                ):
                    self._schedule_idle_disconnect(guild_id, state)
                raise
            state.current = _QueuedTrack(
                track=restart_track,
                stream_url=None,
                is_direct=resolved_is_direct,
            )
            state.started_at = now
            state.playback_offset = seek_seconds
            state.segment_started_at = now
            state.pending_speed_restart = False
            return restart_track

    async def set_speed(
        self,
        guild_id: int,
        channel_id: int | None,
        *,
        speed: float | None = None,
        preserve_pitch: bool | None = None,
    ) -> PlaybackOptions:
        state = self._get_state(guild_id)
        restart_track: Track | None = None
        restart_is_direct = False
        expected_gen = 0

        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            if state.current is not None and state.current.track.is_live:
                raise VoiceError("Cannot change playback speed for live streams.")
            new_options = self._validate_playback_options(
                state.playback,
                speed=speed,
                preserve_pitch=preserve_pitch,
            )
            if new_options == state.playback:
                return state.playback
            if new_options.is_normal_speed and state.playback.is_normal_speed:
                state.playback = new_options
                return new_options
            if state.current is None or (
                not self.voice.is_playing(guild_id)
                and not self.voice.is_paused(guild_id)
            ):
                state.playback = new_options
                return new_options
            if self.voice.is_paused(guild_id):
                state.playback = new_options
                state.pending_speed_restart = True
                return new_options

            restart_track = state.current.track
            restart_is_direct = state.current.is_direct
            expected_gen = state.generation

        stream_url, resolved_is_direct = await self.resolver.prepare_stream(
            restart_track.source_url,
            None,
            is_direct=restart_is_direct,
        )

        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            current = self._current_track(state)
            if current is not None and current.track.is_live:
                raise VoiceError("Cannot change playback speed for live streams.")
            new_options = self._validate_playback_options(
                state.playback,
                speed=speed,
                preserve_pitch=preserve_pitch,
            )
            if state.generation != expected_gen or current is None:
                state.playback = new_options
                if current is not None and self.voice.is_paused(guild_id):
                    state.pending_speed_restart = True
                return new_options
            if self.voice.is_paused(guild_id):
                state.playback = new_options
                state.pending_speed_restart = True
                return new_options

            seek_seconds = self._current_position_seconds(state)
            next_generation = state.generation + 1
            state.generation = next_generation
            state.playback = new_options
            self.voice.stop(guild_id)
            now = time.monotonic()
            try:
                self.voice.play(
                    guild_id,
                    stream_url,
                    after=lambda err: self._on_track_finished(
                        guild_id, next_generation, err
                    ),
                    playback=new_options,
                    seek_seconds=seek_seconds,
                )
            except Exception:
                state.current = None
                state.started_at = None
                self._clear_playback_timing(state)
                if (
                    not state.upcoming
                    and self.voice.connected_channel_id(guild_id) is not None
                ):
                    self._schedule_idle_disconnect(guild_id, state)
                raise
            state.current = _QueuedTrack(
                track=restart_track,
                stream_url=None,
                is_direct=resolved_is_direct,
            )
            state.started_at = now
            state.playback_offset = seek_seconds
            state.segment_started_at = now
            state.pending_speed_restart = False
            return new_options

    async def stop(self, guild_id: int, channel_id: int | None) -> None:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            old_status_channel = self._take_status_channel_id(state)
            self._reset_state(state)
            self.voice.stop(guild_id)
        await self._clear_status_channel(guild_id, old_status_channel)
        await self.voice.disconnect(guild_id)
        async with state.lock:
            self._maybe_evict_guild(guild_id, state)

    async def set_loop(
        self,
        guild_id: int,
        channel_id: int | None,
        mode: LoopMode,
    ) -> LoopMode:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            state.loop_mode = mode
            return mode

    async def remove(
        self,
        guild_id: int,
        channel_id: int | None,
        position: int,
    ) -> Track:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            if position < 1 or position > len(state.upcoming):
                raise VoiceError("Invalid queue position.")
            items = list(state.upcoming)
            removed = items.pop(position - 1)
            state.upcoming = deque(items)
            return removed.track

    async def clear(self, guild_id: int, channel_id: int | None) -> int:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            count = len(state.upcoming)
            state.upcoming.clear()
            return count

    async def shuffle(self, guild_id: int, channel_id: int | None) -> int:
        state = self._get_state(guild_id)
        async with state.lock:
            self._require_same_channel(guild_id, channel_id)
            count = len(state.upcoming)
            if count < 2:
                return count
            items = list(state.upcoming)
            random.shuffle(items)
            state.upcoming = deque(items)
            return count

    @property
    def active_guild_count(self) -> int:
        return len(self._queues)

    def get_queue(self, guild_id: int) -> QueueSnapshot:
        state = self._queues.get(guild_id)
        return QueueSnapshot(
            guild_id=guild_id,
            channel_id=self.voice.connected_channel_id(guild_id),
            current=state.current.track if state and state.current else None,
            upcoming=(
                tuple(item.track for item in state.upcoming) if state else ()
            ),
            loop_mode=state.loop_mode if state else LoopMode.OFF,
            is_paused=self.voice.is_paused(guild_id),
            playback=state.playback if state else DEFAULT_PLAYBACK_OPTIONS,
        )

    async def aclose(self) -> None:
        guilds = list(self._queues.items())
        tasks: list[asyncio.Task[None]] = []
        status_clears: list[tuple[int, int]] = []
        connected_guilds: list[int] = []

        for guild_id, state in guilds:
            async with state.lock:
                if state.idle_task is not None:
                    state.idle_task.cancel()
                    tasks.append(state.idle_task)
                    state.idle_task = None
                if state.icy_task is not None:
                    state.icy_task.cancel()
                    tasks.append(state.icy_task)
                    state.icy_task = None
                status_channel = self._take_status_channel_id(state)
                if status_channel is not None:
                    status_clears.append((guild_id, status_channel))
                self._reset_state(state, cancel_idle=False)
                self.voice.stop(guild_id)
                if self.voice.connected_channel_id(guild_id) is not None:
                    connected_guilds.append(guild_id)

        self._queues.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for guild_id, channel_id in status_clears:
            await self.voice.set_channel_status(guild_id, None, channel_id=channel_id)
        for guild_id in connected_guilds:
            await self.voice.disconnect(guild_id)
        await self.voice.aclose()
        await self.stream_probe.aclose()

import asyncio
import unittest
from typing import Unpack
from unittest.mock import patch

from pydantic import HttpUrl, TypeAdapter

from milihu.exceptions.media import MediaBackendError
from milihu.exceptions.voice import VoiceError
from milihu.types.default.playlist import PLAYLIST_FALLBACK_WARNING
from milihu.types.media import MediaResolveOptions, MediaResult
from milihu.types.playlist import PlaylistEntry, ResolvedPlaylist
from milihu.types.voice import (
    ChannelEmptyCallback,
    ChannelMoveCallback,
    DisconnectCallback,
    LoopMode,
    ProbedAudio,
    StreamMetadata,
    Track,
    TrackEndCallback,
)
from milihu.usecases.music_service import MusicPlayerService

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


class FakeVoiceManager:
    def __init__(self) -> None:
        self.channels: dict[int, int] = {}
        self.member_counts: dict[int, int] = {}
        self.playing: dict[int, bool] = {}
        self.paused: dict[int, bool] = {}
        self.played_urls: list[tuple[int, str]] = []
        self.after_callbacks: dict[int, TrackEndCallback] = {}
        self.on_disconnect: DisconnectCallback | None = None
        self.on_channel_move: ChannelMoveCallback | None = None
        self.on_channel_empty_changed: ChannelEmptyCallback | None = None
        self.disconnect_calls: list[int] = []
        self.status_updates: list[tuple[int, int | None, str | None]] = []
        self.closed = False
        self.raise_on_play: Exception | None = None
        self.connect_gate: asyncio.Event | None = None
        self.connect_started: asyncio.Event | None = None

    def connected_channel_id(self, guild_id: int) -> int | None:
        return self.channels.get(guild_id)

    def non_bot_member_count(self, guild_id: int) -> int:
        return self.member_counts.get(guild_id, 1)

    def is_playing(self, guild_id: int) -> bool:
        return self.playing.get(guild_id, False)

    def is_paused(self, guild_id: int) -> bool:
        return self.paused.get(guild_id, False)

    async def connect(self, guild_id: int, channel_id: int) -> None:
        if self.connect_started is not None:
            self.connect_started.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        self.channels[guild_id] = channel_id

    async def disconnect(self, guild_id: int) -> None:
        self.disconnect_calls.append(guild_id)
        self.channels.pop(guild_id, None)
        self.playing[guild_id] = False
        self.paused[guild_id] = False

    def play(
        self,
        guild_id: int,
        stream_url: str,
        *,
        after: TrackEndCallback,
    ) -> None:
        if self.raise_on_play is not None:
            raise self.raise_on_play
        self.played_urls.append((guild_id, stream_url))
        self.playing[guild_id] = True
        self.paused[guild_id] = False
        self.after_callbacks[guild_id] = after

    def stop(self, guild_id: int) -> None:
        self.playing[guild_id] = False
        self.paused[guild_id] = False

    def pause(self, guild_id: int) -> bool:
        if not self.playing.get(guild_id, False):
            return False
        self.playing[guild_id] = False
        self.paused[guild_id] = True
        return True

    def resume(self, guild_id: int) -> bool:
        if not self.paused.get(guild_id, False):
            return False
        self.paused[guild_id] = False
        self.playing[guild_id] = True
        return True

    async def set_channel_status(
        self,
        guild_id: int,
        status: str | None,
        *,
        channel_id: int | None = None,
    ) -> None:
        self.status_updates.append((guild_id, channel_id, status))

    def set_on_disconnect(self, callback: DisconnectCallback | None) -> None:
        self.on_disconnect = callback

    def set_on_channel_move(self, callback: ChannelMoveCallback | None) -> None:
        self.on_channel_move = callback

    def set_on_channel_empty_changed(
        self, callback: ChannelEmptyCallback | None
    ) -> None:
        self.on_channel_empty_changed = callback

    async def aclose(self) -> None:
        self.closed = True

    async def finish_track(
        self, guild_id: int, error: Exception | None = None
    ) -> None:
        self.playing[guild_id] = False
        cb = self.after_callbacks[guild_id]
        await cb(error)

    async def simulate_kick(self, guild_id: int) -> None:
        self.channels.pop(guild_id, None)
        self.playing[guild_id] = False
        self.paused[guild_id] = False
        if self.on_disconnect is not None:
            await self.on_disconnect(guild_id)

    async def simulate_admin_drag(
        self, guild_id: int, old_channel_id: int, new_channel_id: int
    ) -> None:
        self.channels[guild_id] = new_channel_id
        if self.on_channel_move is not None:
            await self.on_channel_move(guild_id, old_channel_id, new_channel_id)

    async def simulate_empty_changed(self, guild_id: int, is_empty: bool) -> None:
        self.member_counts[guild_id] = 0 if is_empty else 1
        if self.on_channel_empty_changed is not None:
            await self.on_channel_empty_changed(guild_id, is_empty)


class FakeStreamProbe:
    def __init__(self) -> None:
        self.probe_results: dict[str, ProbedAudio | None] = {}
        self.icy_responses: list[StreamMetadata] = []
        self.validated_urls: list[str] = []
        self.icy_calls = 0
        self.closed = False

    async def validate_url(self, url: str) -> None:
        self.validated_urls.append(url)

    async def probe_audio(self, url: HttpUrl) -> ProbedAudio | None:
        return self.probe_results.get(str(url))

    async def fetch_icy_title(self, stream_url: str) -> StreamMetadata:
        self.icy_calls += 1
        if self.icy_responses:
            return self.icy_responses.pop(0)
        return StreamMetadata(supported=False)

    async def aclose(self) -> None:
        self.closed = True


class FakeMediaBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, MediaResolveOptions]] = []
        self.counter = 0

    async def resolve(
        self, url: str, **options: Unpack[MediaResolveOptions]
    ) -> MediaResult:
        self.calls.append((url, options))
        self.counter += 1
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return {
            "status": "file",
            "url": f"https://stream.example/{slug}?n={self.counter}",
            "filename": f"{slug}.opus",
            "delivery": "proxy",
        }


class MusicPlayerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_connect_and_clean_takeover_from_empty_channel(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        probe = FakeStreamProbe()
        player = MusicPlayerService(
            voice, backend, stream_probe=probe, idle_timeout=60
        )

        # User not in voice channel is rejected without calling Cobalt.
        with self.assertRaisesRegex(VoiceError, "voice channel"):
            await player.enqueue(1, None, "https://example.com/a", requested_by=10)
        self.assertEqual(backend.calls, [])

        # Auto-connects to user's voice channel on first play.
        started_events: list[tuple[int, int, Track]] = []

        async def on_start(guild_id: int, channel_id: int, track: Track) -> None:
            started_events.append((guild_id, channel_id, track))

        player.set_on_track_start(on_start)
        first = await player.enqueue(
            1, 100, "https://example.com/song1", requested_by=10
        )
        self.assertTrue(first.started_immediately)
        self.assertEqual(first.position, 0)
        self.assertEqual(voice.connected_channel_id(1), 100)
        self.assertEqual(len(started_events), 1)
        self.assertEqual(started_events[0][:2], (1, 100))

        # Busy in channel 100 with listeners -> refuses move to channel 200.
        voice.member_counts[1] = 2
        with self.assertRaisesRegex(VoiceError, "another voice channel"):
            await player.enqueue(1, 200, "https://example.com/song2", requested_by=20)

        # Empty channel 100 -> clean takeover starts song2 immediately.
        voice.member_counts[1] = 0
        await player.set_loop(1, 100, LoopMode.TRACK)
        second = await player.enqueue(
            1, 200, "https://example.com/song2", requested_by=20
        )
        self.assertTrue(second.started_immediately)
        self.assertEqual(second.position, 0)
        self.assertEqual(voice.connected_channel_id(1), 200)
        snap = player.get_queue(1)
        assert snap.current is not None
        self.assertEqual(snap.current.title, "song2")
        self.assertEqual(snap.upcoming, ())
        self.assertIs(snap.loop_mode, LoopMode.OFF)
        await player.aclose()

    async def test_queue_advance_re_resolves_and_handles_loop_modes(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=60
        )

        await player.enqueue(1, 100, "https://example.com/s1", requested_by=10)
        await player.enqueue(1, 100, "https://example.com/s2", requested_by=10)
        self.assertEqual(voice.played_urls, [(1, "https://stream.example/s1?n=1")])

        # Loop TRACK repeats s1 with a freshly resolved Cobalt URL.
        await player.set_loop(1, 100, LoopMode.TRACK)
        await voice.finish_track(1)
        self.assertEqual(voice.played_urls[-1], (1, "https://stream.example/s1?n=3"))
        snap_track = player.get_queue(1)
        assert snap_track.current is not None
        self.assertEqual(snap_track.current.title, "s1")

        # Loop QUEUE rotates s1 to back and plays s2.
        await player.set_loop(1, 100, LoopMode.QUEUE)
        await voice.finish_track(1)
        snap = player.get_queue(1)
        assert snap.current is not None
        self.assertEqual(snap.current.title, "s2")
        self.assertEqual([t.title for t in snap.upcoming], ["s1"])

        # Skip advances to s1 and rotates s2 to back while in QUEUE loop.
        skipped = await player.skip(1, 100)
        self.assertEqual(skipped.title, "s2")
        snap2 = player.get_queue(1)
        assert snap2.current is not None
        self.assertEqual(snap2.current.title, "s1")
        self.assertEqual([t.title for t in snap2.upcoming], ["s2"])

        # Turn off loop and exhaust queue.
        await player.set_loop(1, 100, LoopMode.OFF)
        await voice.finish_track(1)
        await voice.finish_track(1)
        self.assertIsNone(player.get_queue(1).current)
        await player.aclose()

    async def test_queue_management_controls_and_guild_eviction(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=60
        )

        await player.enqueue(1, 100, "https://example.com/a", requested_by=1)
        await player.enqueue(1, 100, "https://example.com/b", requested_by=1)
        await player.enqueue(1, 100, "https://example.com/c", requested_by=1)
        await player.enqueue(1, 100, "https://example.com/d", requested_by=1)

        # Control from wrong channel is rejected; get_queue is always allowed.
        with self.assertRaisesRegex(VoiceError, "same voice channel"):
            await player.pause(1, 999)
        self.assertEqual(len(player.get_queue(1).upcoming), 3)

        # Pause & resume.
        paused = await player.pause(1, 100)
        self.assertEqual(paused.title, "a")
        with self.assertRaisesRegex(VoiceError, "already paused"):
            await player.pause(1, 100)
        resumed = await player.resume(1, 100)
        self.assertEqual(resumed.title, "a")

        # Remove 1-based item.
        removed = await player.remove(1, 100, 2)
        self.assertEqual(removed.title, "c")
        self.assertEqual([t.title for t in player.get_queue(1).upcoming], ["b", "d"])

        # Shuffle & clear.
        self.assertEqual(await player.shuffle(1, 100), 2)
        self.assertEqual(await player.clear(1, 100), 2)
        self.assertEqual(player.get_queue(1).upcoming, ())

        # Stop clears state, disconnects, and evicts guild from active queues.
        await player.stop(1, 100)
        self.assertIsNone(voice.connected_channel_id(1))
        self.assertIsNone(player.get_queue(1).current)
        self.assertEqual(player.active_guild_count, 0)
        await player.aclose()

    async def test_idle_timeout_disconnects_and_cancels_on_new_enqueue(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=0.05
        )

        await player.enqueue(1, 100, "https://example.com/a", requested_by=1)
        await voice.finish_track(1)
        await asyncio.sleep(0.02)

        # New track before timeout cancels disconnect.
        await player.enqueue(1, 100, "https://example.com/b", requested_by=1)
        await asyncio.sleep(0.06)
        self.assertEqual(voice.connected_channel_id(1), 100)
        self.assertEqual(voice.disconnect_calls, [])

        # Finish second track and wait past timeout -> disconnects & evicts guild.
        await voice.finish_track(1)
        await asyncio.sleep(0.08)
        self.assertIsNone(voice.connected_channel_id(1))
        self.assertEqual(voice.disconnect_calls, [1])
        self.assertEqual(player.active_guild_count, 0)
        await player.aclose()

    async def test_auto_disconnect_when_all_humans_leave_and_cancel_on_rejoin(
        self,
    ) -> None:
        voice = FakeVoiceManager()
        probe = FakeStreamProbe()
        radio_url = "http://jpopsuki.fm:8000/stream"
        probe.probe_results[radio_url] = ProbedAudio(
            stream_url=radio_url, title="Jpopsuki Radio", is_live=True
        )
        probe.icy_responses = [
            StreamMetadata(supported=True, title="Track 1"),
            StreamMetadata(supported=False),
        ]
        player = MusicPlayerService(
            voice,
            None,
            stream_probe=probe,
            idle_timeout=0.05,
            icy_poll_interval=0.01,
        )

        await player.enqueue(1, 100, radio_url, requested_by=1)
        await asyncio.sleep(0.02)
        self.assertEqual(voice.status_updates, [(1, 100, "Track 1")])

        # All humans leave -> idle timer starts; human rejoins -> timer cancels.
        await voice.simulate_empty_changed(1, True)
        await asyncio.sleep(0.02)
        await voice.simulate_empty_changed(1, False)
        await asyncio.sleep(0.06)
        self.assertEqual(voice.connected_channel_id(1), 100)
        self.assertIsNotNone(player.get_queue(1).current)

        # All humans leave and stay gone -> stops radio, clears status, disconnects!
        await voice.simulate_empty_changed(1, True)
        await asyncio.sleep(0.08)
        self.assertIsNone(voice.connected_channel_id(1))
        self.assertEqual(voice.disconnect_calls, [1])
        self.assertEqual(voice.status_updates[-1], (1, 100, None))
        self.assertEqual(player.active_guild_count, 0)
        await player.aclose()

    async def test_consecutive_fast_failures_break_infinite_loop(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=0.05
        )

        with patch("milihu.usecases.music_service.time.monotonic", return_value=100.0):
            await player.enqueue(1, 100, "https://example.com/bad", requested_by=1)
            await player.set_loop(1, 100, LoopMode.TRACK)
            # 1st and 2nd fast failures retry.
            await voice.finish_track(1, error=RuntimeError("ffmpeg exited"))
            self.assertEqual(len(voice.played_urls), 2)
            await voice.finish_track(1, error=RuntimeError("ffmpeg exited"))
            self.assertEqual(len(voice.played_urls), 3)
            # 3rd fast failure breaks loop, disables TRACK loop, and clears current.
            await voice.finish_track(1, error=RuntimeError("ffmpeg exited"))
            self.assertEqual(len(voice.played_urls), 3)
            snap = player.get_queue(1)
            self.assertIsNone(snap.current)
            self.assertIs(snap.loop_mode, LoopMode.OFF)

        # Healthy playback duration resets fast-failure counter.
        now = 200.0
        with patch(
            "milihu.usecases.music_service.time.monotonic", side_effect=lambda: now
        ):
            await player.enqueue(1, 100, "https://example.com/good", requested_by=1)
            await player.set_loop(1, 100, LoopMode.TRACK)
            for _ in range(5):
                now += 10.0
                await voice.finish_track(1)
            self.assertIsNotNone(player.get_queue(1).current)
            self.assertIs(player.get_queue(1).loop_mode, LoopMode.TRACK)

        await player.aclose()

    async def test_lock_not_held_during_connect_and_failed_play_schedules_idle(
        self,
    ) -> None:
        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=0.05
        )

        # Verify state.lock is NOT held while voice.connect() is awaiting:
        # pause() acquires state.lock and returns immediately without blocking.
        voice.connect_started = asyncio.Event()
        voice.connect_gate = asyncio.Event()
        enqueue_task = asyncio.create_task(
            player.enqueue(1, 100, "https://example.com/slow", requested_by=1)
        )
        await asyncio.wait_for(voice.connect_started.wait(), timeout=1.0)
        with self.assertRaisesRegex(VoiceError, "not connected"):
            await asyncio.wait_for(player.pause(1, 100), timeout=0.2)
        voice.connect_gate.set()
        await enqueue_task

        # Failed voice.play() on first enqueue schedules idle disconnect.
        voice2 = FakeVoiceManager()
        voice2.raise_on_play = VoiceError("ffmpeg crash")
        player2 = MusicPlayerService(
            voice2, backend, stream_probe=FakeStreamProbe(), idle_timeout=0.05
        )
        with self.assertRaisesRegex(VoiceError, "ffmpeg crash"):
            await player2.enqueue(2, 200, "https://example.com/fail", requested_by=1)
        self.assertEqual(voice2.connected_channel_id(2), 200)
        await asyncio.sleep(0.08)
        self.assertEqual(voice2.disconnect_calls, [2])

        await player.aclose()
        await player2.aclose()

    async def test_external_disconnect_and_admin_drag_clean_status(self) -> None:
        voice = FakeVoiceManager()
        probe = FakeStreamProbe()
        radio_url = "http://jpopsuki.fm:8000/stream"
        probe.probe_results[radio_url] = ProbedAudio(
            stream_url=radio_url, title="Jpopsuki Radio", is_live=True
        )
        probe.icy_responses = [
            StreamMetadata(supported=True, title="YOASOBI - Idol"),
            StreamMetadata(supported=True, title="YOASOBI - Idol"),
        ]
        player = MusicPlayerService(
            voice,
            None,
            stream_probe=probe,
            idle_timeout=60,
            icy_poll_interval=0.02,
        )

        await player.enqueue(1, 100, radio_url, requested_by=1)
        await asyncio.sleep(0.01)
        self.assertEqual(voice.status_updates, [(1, 100, "YOASOBI - Idol")])

        # Admin drags bot from channel 100 to 200 -> old channel 100 status cleared,
        # and next ICY poll sets channel 200 status even if title is unchanged.
        await voice.simulate_admin_drag(1, 100, 200)
        self.assertEqual(voice.status_updates[-1], (1, 100, None))
        await asyncio.sleep(0.04)
        self.assertEqual(voice.status_updates[-1], (1, 200, "YOASOBI - Idol"))

        # External disconnect clears status and evicts guild.
        await voice.simulate_kick(1)
        self.assertEqual(voice.status_updates[-1], (1, 200, None))
        self.assertIsNone(player.get_queue(1).current)
        self.assertEqual(player.active_guild_count, 0)
        await player.aclose()

    async def test_radio_fallback_icy_pause_and_aclose(self) -> None:
        class RejectingBackend:
            async def resolve(
                self, url: str, **options: Unpack[MediaResolveOptions]
            ) -> MediaResult:
                return {"status": "error", "code": "error.api.link.unsupported"}

        voice = FakeVoiceManager()
        probe = FakeStreamProbe()
        radio_url = "http://jpopsuki.fm:8000/stream"
        probe.probe_results[radio_url] = ProbedAudio(
            stream_url=radio_url, title="Jpopsuki Radio", is_live=True
        )
        probe.icy_responses = [
            StreamMetadata(supported=True, title="Song 1"),
            StreamMetadata(supported=True, title="Song 1"),
            StreamMetadata(supported=True, title="Song 2"),
            StreamMetadata(supported=False),
        ]
        player = MusicPlayerService(
            voice,
            RejectingBackend(),
            stream_probe=probe,
            idle_timeout=60,
            icy_poll_interval=0.015,
        )

        res = await player.enqueue(1, 100, radio_url, requested_by=42)
        self.assertTrue(res.started_immediately)
        self.assertEqual(res.track.title, "Jpopsuki Radio")
        self.assertTrue(res.track.is_live)
        self.assertEqual(probe.validated_urls, [radio_url])

        # Wait for first ICY update, then pause and confirm ICY polling pauses.
        await asyncio.sleep(0.01)
        await player.pause(1, 100)
        calls_while_paused = probe.icy_calls
        await asyncio.sleep(0.04)
        self.assertEqual(probe.icy_calls, calls_while_paused)

        # Resume -> ICY polling resumes and updates channel status.
        await player.resume(1, 100)
        await asyncio.sleep(0.06)
        self.assertEqual(
            voice.status_updates,
            [(1, 100, "Song 1"), (1, 100, "Song 2")],
        )

        # Non-audio URL rejected by both Cobalt and probe raises Cobalt's error.
        with self.assertRaisesRegex(VoiceError, "error.api.link.unsupported"):
            await player.enqueue(1, 100, "https://example.com/page", requested_by=42)

        # aclose clears status, disconnects, and closes voice + stream_probe.
        await player.aclose()
        self.assertEqual(voice.status_updates[-1], (1, 100, None))
        self.assertEqual(voice.disconnect_calls, [1])
        self.assertTrue(voice.closed)
        self.assertTrue(probe.closed)

    async def test_playlist_enqueue_lazy_resolution_and_fallback_policy(self) -> None:
        class FakePlaylistResolver:
            def __init__(self) -> None:
                self.results: dict[str, ResolvedPlaylist | Exception | None] = {}

            async def resolve(self, url: HttpUrl) -> ResolvedPlaylist | None:
                outcome = self.results.get(str(url))
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        playlist_url = "https://youtu.be/v1?list=RD123"
        playlist_url2 = "https://www.youtube.com/playlist?list=PL456"
        fallback_url = "https://www.youtube.com/watch?v=solo&list=WL"

        resolver = FakePlaylistResolver()
        resolver.results[playlist_url] = ResolvedPlaylist(
            title="Mix - Indie",
            entries=(
                PlaylistEntry(
                    source_url=_URL_ADAPTER.validate_python(
                        "https://www.youtube.com/watch?v=v1"
                    ),
                    title="Song One",
                ),
                PlaylistEntry(
                    source_url=_URL_ADAPTER.validate_python(
                        "https://www.youtube.com/watch?v=v2"
                    ),
                    title="Song Two",
                ),
            ),
        )
        resolver.results[playlist_url2] = ResolvedPlaylist(
            title="Second List",
            entries=(
                PlaylistEntry(
                    source_url=_URL_ADAPTER.validate_python(
                        "https://www.youtube.com/watch?v=v3"
                    ),
                    title="Song Three",
                ),
                PlaylistEntry(
                    source_url=_URL_ADAPTER.validate_python(
                        "https://www.youtube.com/watch?v=v4"
                    ),
                    title="Song Four",
                ),
            ),
        )
        resolver.results[fallback_url] = VoiceError(
            "Could not load tracks from this playlist."
        )

        voice = FakeVoiceManager()
        backend = FakeMediaBackend()
        player = MusicPlayerService(
            voice,
            backend,
            stream_probe=FakeStreamProbe(),
            playlist_resolver=resolver,
            idle_timeout=60,
        )

        # Idle bot: starts track 1 immediately (1 Cobalt call) and queues rest lazily.
        first = await player.enqueue(1, 100, playlist_url, requested_by=7)
        self.assertTrue(first.started_immediately)
        self.assertEqual(first.enqueued_count, 2)
        self.assertEqual(first.playlist_title, "Mix - Indie")
        self.assertEqual(first.track.title, "Song One")
        self.assertEqual(len(backend.calls), 1)

        # Already playing: queues entire second playlist with 0 Cobalt calls.
        second = await player.enqueue(1, 100, playlist_url2, requested_by=8)
        self.assertFalse(second.started_immediately)
        self.assertEqual(second.position, 2)
        self.assertEqual(second.enqueued_count, 2)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(
            [t.title for t in player.get_queue(1).upcoming],
            ["Song Two", "Song Three", "Song Four"],
        )

        # Advancing resolves track 2 on demand.
        await voice.finish_track(1)
        self.assertEqual(len(backend.calls), 2)
        snap = player.get_queue(1)
        assert snap.current is not None
        self.assertEqual(snap.current.title, "Song Two")

        # Playlist failure falls back to single track with warning when Cobalt succeeds.
        fb = await player.enqueue(1, 100, fallback_url, requested_by=9)
        self.assertFalse(fb.started_immediately)
        self.assertEqual(fb.enqueued_count, 1)
        self.assertEqual(fb.warning, PLAYLIST_FALLBACK_WARNING)

        # Playlist failure propagates original playlist error when Cobalt also fails.
        class RejectingBackend:
            async def resolve(
                self, url: str, **options: Unpack[MediaResolveOptions]
            ) -> MediaResult:
                return {"status": "error", "code": "error.api.link.unsupported"}

        player2 = MusicPlayerService(
            FakeVoiceManager(),
            RejectingBackend(),
            stream_probe=FakeStreamProbe(),
            playlist_resolver=resolver,
            idle_timeout=60,
        )
        with self.assertRaisesRegex(
            VoiceError, "Could not load tracks from this playlist"
        ):
            await player2.enqueue(2, 200, fallback_url, requested_by=9)

        await player.aclose()
        await player2.aclose()

    async def test_play_next_skips_many_dead_tracks_but_stops_on_backend_outage(
        self,
    ) -> None:
        class SelectiveBackend:
            def __init__(self) -> None:
                self.mode = "dead_videos"

            async def resolve(
                self, url: str, **options: Unpack[MediaResolveOptions]
            ) -> MediaResult:
                if "ok" in url:
                    return {
                        "status": "file",
                        "url": f"https://stream.example/{url.rsplit('/', 1)[-1]}",
                        "filename": "ok.opus",
                        "delivery": "proxy",
                    }
                if self.mode == "dead_videos":
                    return {"status": "error", "code": "error.api.youtube.unavailable"}
                if self.mode == "http_503":
                    raise MediaBackendError("Service Unavailable", status=503)
                raise MediaBackendError("connection refused")

        class StaticPlaylistResolver:
            def __init__(self, entries: tuple[PlaylistEntry, ...]) -> None:
                self.entries = entries

            async def resolve(self, url: HttpUrl) -> ResolvedPlaylist | None:
                return ResolvedPlaylist(title="Big List", entries=self.entries)

        # 4 dead tracks in a row followed by 1 playable -> skips all 4, plays #5.
        entries_dead = tuple(
            PlaylistEntry(
                source_url=_URL_ADAPTER.validate_python(f"https://example.com/{name}"),
                title=name,
            )
            for name in ("ok0", "dead1", "dead2", "dead3", "dead4", "ok5")
        )
        voice = FakeVoiceManager()
        backend = SelectiveBackend()
        player = MusicPlayerService(
            voice,
            backend,
            stream_probe=FakeStreamProbe(),
            playlist_resolver=StaticPlaylistResolver(entries_dead),
            idle_timeout=60,
        )

        await player.enqueue(1, 100, "https://youtu.be/ok0?list=PL1", requested_by=1)
        await voice.finish_track(1)
        snap = player.get_queue(1)
        assert snap.current is not None
        self.assertEqual(snap.current.title, "ok5")
        self.assertEqual(snap.upcoming, ())

        # Backend outage stops after 3 failures and preserves the rest of the queue.
        entries_outage = tuple(
            PlaylistEntry(
                source_url=_URL_ADAPTER.validate_python(f"https://example.com/{name}"),
                title=name,
            )
            for name in ("ok0", "f1", "f2", "f3", "kept1", "kept2")
        )
        voice2 = FakeVoiceManager()
        backend2 = SelectiveBackend()
        player2 = MusicPlayerService(
            voice2,
            backend2,
            stream_probe=FakeStreamProbe(),
            playlist_resolver=StaticPlaylistResolver(entries_outage),
            idle_timeout=60,
        )
        await player2.enqueue(2, 200, "https://youtu.be/ok0?list=PL2", requested_by=1)
        backend2.mode = "outage"
        await voice2.finish_track(2)
        snap2 = player2.get_queue(2)
        self.assertIsNone(snap2.current)
        self.assertEqual([t.title for t in snap2.upcoming], ["kept1", "kept2"])

        await player.aclose()
        await player2.aclose()

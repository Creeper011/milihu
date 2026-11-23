import asyncio
import unittest
from typing import override
from unittest.mock import patch

from pydantic import HttpUrl, TypeAdapter

from milihu.exceptions.voice import BackendUnavailableError, VoiceError
from milihu.types.default.voice import DEFAULT_PLAYBACK_OPTIONS
from milihu.types.playlist import (
    AudioLoadResult,
    AudioTrackItem,
    ResolvedPlaylist,
    ResolvedStream,
    StreamHeaders,
)
from milihu.types.voice import (
    ChannelEmptyCallback,
    ChannelMoveCallback,
    DisconnectCallback,
    LoopMode,
    PlaybackOptions,
    ProbedAudio,
    SeekOffset,
    StreamMetadata,
    Track,
    TrackEndCallback,
)
from milihu.usecases.music_service import MusicPlayerService

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


def _url(raw: str) -> HttpUrl:
    return _URL_ADAPTER.validate_python(raw)


class FakeVoiceManager:
    def __init__(self) -> None:
        self.channels: dict[int, int] = {}
        self.member_counts: dict[int, int] = {}
        self.playing: dict[int, bool] = {}
        self.paused: dict[int, bool] = {}
        self.played_urls: list[tuple[int, str]] = []
        self.play_calls: list[
            tuple[int, str, PlaybackOptions, float, StreamHeaders]
        ] = []
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
        playback: PlaybackOptions = DEFAULT_PLAYBACK_OPTIONS,
        seek_seconds: SeekOffset = 0.0,
        headers: StreamHeaders = (),
    ) -> None:
        if self.raise_on_play is not None:
            raise self.raise_on_play
        self.played_urls.append((guild_id, stream_url))
        self.play_calls.append((guild_id, stream_url, playback, seek_seconds, headers))
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


class FakeAudioBackend:
    def __init__(self) -> None:
        self.load_calls: list[str] = []
        self.search_queries: list[str] = []
        self.stream_calls: list[str] = []
        self.playlists: dict[str, ResolvedPlaylist | Exception] = {}
        self.search_results: dict[str, tuple[AudioTrackItem, ...] | Exception] = {}
        self.counter = 0

    async def load(self, url: HttpUrl) -> AudioLoadResult:
        raw = str(url)
        self.load_calls.append(raw)
        if raw in self.playlists:
            outcome = self.playlists[raw]
            if isinstance(outcome, Exception):
                raise outcome
            return AudioLoadResult(kind="playlist", playlist=outcome)
        slug = raw.rstrip("/").rsplit("/", 1)[-1]
        return AudioLoadResult(
            kind="track",
            track=AudioTrackItem(
                source_url=url,
                title=slug,
                encoded=f"enc:{slug}",
            ),
        )

    async def search(self, query: str) -> tuple[AudioTrackItem, ...]:
        self.search_queries.append(query)
        if query in self.search_results:
            outcome = self.search_results[query]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return (
            AudioTrackItem(
                source_url=_url("https://www.youtube.com/watch?v=abc123"),
                title="abc123",
                encoded="enc:abc123",
            ),
        )

    async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream:
        self.stream_calls.append(item.encoded)
        self.counter += 1
        slug = item.encoded.removeprefix("enc:")
        return ResolvedStream(
            stream_url=f"http://127.0.0.1:2333/v4/loadstream?slug={slug}&n={self.counter}",
            title=item.title,
            is_live=item.is_live,
            is_direct=False,
            headers=(("Authorization", "nl-token"),),
        )


class MusicPlayerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_resolves_before_enqueue_and_rejects_bad_input(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeAudioBackend()
        backend.search_results["missing song"] = VoiceError(
            "No videos found for this search."
        )
        player = MusicPlayerService(voice, backend, stream_probe=FakeStreamProbe())
        with self.assertRaisesRegex(VoiceError, "voice channel"):
            await player.enqueue(1, None, "my favorite song", requested_by=1)
        self.assertEqual(backend.search_queries, [])
        for value in ("  ", "https://", "missing song"):
            with self.assertRaises(VoiceError):
                await player.enqueue(1, 100, value, requested_by=1)
        self.assertEqual(voice.channels, {})
        self.assertEqual(backend.stream_calls, [])
        result = await player.enqueue(1, 100, "  my favorite song  ", requested_by=1)
        self.assertEqual(
            backend.search_queries, ["missing song", "my favorite song"]
        )
        self.assertEqual(
            str(result.track.source_url), "https://www.youtube.com/watch?v=abc123"
        )
        self.assertEqual(backend.stream_calls, ["enc:abc123"])
        self.assertEqual(
            voice.play_calls[0][4], (("Authorization", "nl-token"),)
        )
        await player.enqueue(1, 100, "https://example.com/direct", requested_by=1)
        self.assertEqual(
            backend.search_queries, ["missing song", "my favorite song"]
        )
        await player.aclose()

    async def test_auto_connect_and_clean_takeover_from_empty_channel(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeAudioBackend()
        probe = FakeStreamProbe()
        player = MusicPlayerService(
            voice, backend, stream_probe=probe, idle_timeout=60
        )

        # User not in voice channel is rejected without calling backend.
        with self.assertRaisesRegex(VoiceError, "voice channel"):
            await player.enqueue(1, None, "https://example.com/a", requested_by=10)
        self.assertEqual(backend.load_calls, [])

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
        backend = FakeAudioBackend()
        player = MusicPlayerService(
            voice, backend, stream_probe=FakeStreamProbe(), idle_timeout=60
        )

        await player.enqueue(1, 100, "https://example.com/s1", requested_by=10)
        await player.enqueue(1, 100, "https://example.com/s2", requested_by=10)
        self.assertEqual(
            voice.played_urls,
            [(1, "http://127.0.0.1:2333/v4/loadstream?slug=s1&n=1")],
        )

        # Loop TRACK repeats s1 with a freshly resolved stream URL via cached token.
        await player.set_loop(1, 100, LoopMode.TRACK)
        await voice.finish_track(1)
        self.assertEqual(
            voice.played_urls[-1],
            (1, "http://127.0.0.1:2333/v4/loadstream?slug=s1&n=3"),
        )
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
        backend = FakeAudioBackend()
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
        backend = FakeAudioBackend()
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
        backend = FakeAudioBackend()
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
        backend = FakeAudioBackend()
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
        class EmptyAudioBackend:
            async def load(self, url: HttpUrl) -> AudioLoadResult:
                if "page" in str(url):
                    raise VoiceError("Could not resolve audio: unsupported link")
                return AudioLoadResult(kind="empty")

            async def search(self, query: str) -> tuple[AudioTrackItem, ...]:
                return ()

            async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream:
                raise NotImplementedError

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
            EmptyAudioBackend(),
            stream_probe=probe,
            idle_timeout=60,
            icy_poll_interval=0.015,
        )

        res = await player.enqueue(1, 100, radio_url, requested_by=42)
        self.assertTrue(res.started_immediately)
        self.assertEqual(res.track.title, "Jpopsuki Radio")
        self.assertTrue(res.track.is_live)
        self.assertEqual(probe.validated_urls, [radio_url])
        # Direct streams never attach NodeLink Authorization headers.
        self.assertEqual(voice.play_calls[-1][4], ())

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

        # Non-audio URL rejected by both NodeLink and probe raises backend's error.
        with self.assertRaisesRegex(VoiceError, "unsupported link"):
            await player.enqueue(1, 100, "https://example.com/page", requested_by=42)

        # aclose clears status, disconnects, and closes voice + stream_probe.
        await player.aclose()
        self.assertEqual(voice.status_updates[-1], (1, 100, None))
        self.assertEqual(voice.disconnect_calls, [1])
        self.assertTrue(voice.closed)
        self.assertTrue(probe.closed)

    async def test_spotify_playlist_enqueue_and_lazy_resolution(self) -> None:
        playlist_url = "https://open.spotify.com/playlist/40GRBpPK3knyYNfPFAlGlG"
        playlist_url2 = "https://www.youtube.com/playlist?list=PL456"

        backend = FakeAudioBackend()
        backend.playlists[playlist_url] = ResolvedPlaylist(
            title="sophia hipotética",
            entries=(
                AudioTrackItem(
                    source_url=_url(
                        "https://open.spotify.com/track/1UNEuG9DYOWiikf00ayr52"
                    ),
                    title="Ravyn Lenae - Love Me Not",
                    encoded="enc:sp1",
                ),
                AudioTrackItem(
                    source_url=_url(
                        "https://open.spotify.com/track/3jfZ9M23l0L7RxzYMTgBTv"
                    ),
                    title="Joy Again - Looking Out for You",
                    encoded="enc:sp2",
                ),
            ),
        )
        backend.playlists[playlist_url2] = ResolvedPlaylist(
            title="Second List",
            entries=(
                AudioTrackItem(
                    source_url=_url("https://www.youtube.com/watch?v=v3"),
                    title="Song Three",
                    encoded="enc:v3",
                ),
                AudioTrackItem(
                    source_url=_url("https://www.youtube.com/watch?v=v4"),
                    title="Song Four",
                    encoded="enc:v4",
                ),
            ),
        )

        voice = FakeVoiceManager()
        player = MusicPlayerService(
            voice,
            backend,
            stream_probe=FakeStreamProbe(),
            idle_timeout=60,
        )

        # Idle bot: starts track 1 immediately (1 stream call) and queues rest lazily.
        first = await player.enqueue(1, 100, playlist_url, requested_by=7)
        self.assertTrue(first.started_immediately)
        self.assertEqual(first.enqueued_count, 2)
        self.assertEqual(first.playlist_title, "sophia hipotética")
        self.assertEqual(first.track.title, "Ravyn Lenae - Love Me Not")
        self.assertEqual(backend.stream_calls, ["enc:sp1"])

        # Already playing: queues entire second playlist with 0 stream resolutions.
        second = await player.enqueue(1, 100, playlist_url2, requested_by=8)
        self.assertFalse(second.started_immediately)
        self.assertEqual(second.position, 2)
        self.assertEqual(second.enqueued_count, 2)
        self.assertEqual(backend.stream_calls, ["enc:sp1"])
        self.assertEqual(
            [t.title for t in player.get_queue(1).upcoming],
            ["Joy Again - Looking Out for You", "Song Three", "Song Four"],
        )

        # Advancing resolves track 2 on demand without calling load() again.
        await voice.finish_track(1)
        self.assertEqual(backend.stream_calls, ["enc:sp1", "enc:sp2"])
        snap = player.get_queue(1)
        assert snap.current is not None
        self.assertEqual(snap.current.title, "Joy Again - Looking Out for You")

        await player.aclose()

    async def test_play_next_skips_many_dead_tracks_but_stops_on_backend_outage(
        self,
    ) -> None:
        class SelectiveAudioBackend(FakeAudioBackend):
            def __init__(self) -> None:
                super().__init__()
                self.mode = "dead_videos"

            @override
            async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream:
                if "ok" in item.encoded:
                    return await super().resolve_stream(item)
                if self.mode == "dead_videos":
                    raise VoiceError("Could not resolve audio: video unavailable")
                raise BackendUnavailableError(
                    "Audio backend is temporarily unavailable."
                )

        # 4 dead tracks in a row followed by 1 playable -> skips all 4, plays #5.
        entries_dead = tuple(
            AudioTrackItem(
                source_url=_url(f"https://example.com/{name}"),
                title=name,
                encoded=f"enc:{name}",
            )
            for name in ("ok0", "dead1", "dead2", "dead3", "dead4", "ok5")
        )
        voice = FakeVoiceManager()
        backend = SelectiveAudioBackend()
        backend.playlists["https://youtu.be/ok0?list=PL1"] = ResolvedPlaylist(
            title="Big List", entries=entries_dead
        )
        player = MusicPlayerService(
            voice,
            backend,
            stream_probe=FakeStreamProbe(),
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
            AudioTrackItem(
                source_url=_url(f"https://example.com/{name}"),
                title=name,
                encoded=f"enc:{name}",
            )
            for name in ("ok0", "f1", "f2", "f3", "kept1", "kept2")
        )
        voice2 = FakeVoiceManager()
        backend2 = SelectiveAudioBackend()
        backend2.playlists["https://youtu.be/ok0?list=PL2"] = ResolvedPlaylist(
            title="Big List", entries=entries_outage
        )
        player2 = MusicPlayerService(
            voice2,
            backend2,
            stream_probe=FakeStreamProbe(),
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

    async def test_search_falls_back_to_next_candidate_when_stream_fails(self) -> None:
        class FailingFirstBackend(FakeAudioBackend):
            def __init__(self, *, outage: bool = False) -> None:
                super().__init__()
                self.outage = outage

            @override
            async def resolve_stream(self, item: AudioTrackItem) -> ResolvedStream:
                self.stream_calls.append(item.encoded)
                if self.outage:
                    raise BackendUnavailableError("service unavailable")
                if "dead" in item.encoded:
                    raise VoiceError("Could not resolve audio: unavailable")
                return ResolvedStream(
                    stream_url=f"http://127.0.0.1:2333/v4/loadstream?e={item.encoded}",
                    title=item.title,
                    headers=(("Authorization", "nl-token"),),
                )

        def _candidates(*names: str) -> tuple[AudioTrackItem, ...]:
            return tuple(
                AudioTrackItem(
                    source_url=_url(f"https://www.youtube.com/watch?v={name}"),
                    title=name,
                    encoded=f"enc:{name}",
                )
                for name in names
            )

        # Candidate #1 is unplayable -> automatically falls back to candidate #2.
        voice = FakeVoiceManager()
        backend = FailingFirstBackend()
        backend.search_results["some song"] = _candidates(
            "dead_01", "good_02", "good_03"
        )
        player = MusicPlayerService(voice, backend, stream_probe=FakeStreamProbe())
        result = await player.enqueue(1, 100, "some song", requested_by=7)
        self.assertEqual(
            str(result.track.source_url),
            "https://www.youtube.com/watch?v=good_02",
        )
        self.assertEqual(result.track.title, "good_02")
        self.assertEqual(backend.stream_calls, ["enc:dead_01", "enc:good_02"])

        # Consecutive backend outages abort after max_backend_failures (3).
        voice_outage = FakeVoiceManager()
        outage_backend = FailingFirstBackend(outage=True)
        outage_backend.search_results["some song"] = _candidates(
            *(f"cand_{i}" for i in range(5))
        )
        player_outage = MusicPlayerService(
            voice_outage, outage_backend, stream_probe=FakeStreamProbe()
        )
        with self.assertRaisesRegex(VoiceError, "service unavailable"):
            await player_outage.enqueue(2, 200, "some song", requested_by=7)
        self.assertEqual(len(outage_backend.stream_calls), 3)
        self.assertEqual(voice_outage.channels, {})

        # All candidates unplayable -> raises last VoiceError without connecting.
        voice_dead = FakeVoiceManager()
        dead_backend = FailingFirstBackend()
        dead_backend.search_results["all dead"] = _candidates("dead_01", "dead_02")
        player_dead = MusicPlayerService(
            voice_dead, dead_backend, stream_probe=FakeStreamProbe()
        )
        with self.assertRaisesRegex(VoiceError, "unavailable"):
            await player_dead.enqueue(3, 300, "all dead", requested_by=7)
        self.assertEqual(len(dead_backend.stream_calls), 2)
        self.assertEqual(voice_dead.channels, {})

        await player.aclose()
        await player_outage.aclose()
        await player_dead.aclose()

    async def test_set_speed_applies_immediately_persists_and_resets(self) -> None:
        voice = FakeVoiceManager()
        backend = FakeAudioBackend()
        started_tracks: list[str] = []

        async def on_track_start(_guild_id: int, _ch: int, track: Track) -> None:
            started_tracks.append(track.title)

        player = MusicPlayerService(
            voice,
            backend,
            stream_probe=FakeStreamProbe(),
            on_track_start=on_track_start,
        )

        now = 100.0
        with patch(
            "milihu.usecases.music_service.time.monotonic", side_effect=lambda: now
        ):
            await player.enqueue(1, 10, "https://example.com/1", requested_by=1)
            await player.enqueue(1, 10, "https://example.com/2", requested_by=1)
            self.assertEqual(len(voice.play_calls), 1)
            self.assertEqual(voice.play_calls[0][2], DEFAULT_PLAYBACK_OPTIONS)
            self.assertEqual(voice.play_calls[0][3], 0.0)
            self.assertEqual(
                voice.play_calls[0][4], (("Authorization", "nl-token"),)
            )
            old_after = voice.after_callbacks[1]

            # Advance 10 seconds at 1.0x, then change speed to 1.5x.
            now = 110.0
            updated = await player.set_speed(1, 10, speed=1.5)
            self.assertEqual(updated, PlaybackOptions(speed=1.5, preserve_pitch=True))
            self.assertEqual(len(voice.play_calls), 2)
            # Re-resolved a fresh stream URL on restart with Authorization preserved.
            self.assertNotEqual(voice.play_calls[1][1], voice.play_calls[0][1])
            self.assertEqual(voice.play_calls[1][2], updated)
            self.assertAlmostEqual(voice.play_calls[1][3], 10.0)
            self.assertEqual(
                voice.play_calls[1][4], (("Authorization", "nl-token"),)
            )
            # Restarting for speed does not re-announce Now Playing.
            self.assertEqual(len(started_tracks), 1)

            # Stale `after` callback from the stopped player is ignored.
            await old_after(None)
            self.assertEqual(len(voice.play_calls), 2)

            # Advance 4 real seconds at 1.5x -> +6.0 media seconds -> 16.0s total.
            now = 114.0
            updated2 = await player.set_speed(1, 10, preserve_pitch=False)
            self.assertEqual(
                updated2, PlaybackOptions(speed=1.5, preserve_pitch=False)
            )
            self.assertEqual(len(voice.play_calls), 3)
            self.assertEqual(voice.play_calls[2][2], updated2)
            self.assertAlmostEqual(voice.play_calls[2][3], 16.0)
            self.assertEqual(player.get_queue(1).playback, updated2)

            # No-op when setting identical values does not restart playback.
            same = await player.set_speed(1, 10, speed=1.5, preserve_pitch=False)
            self.assertEqual(same, updated2)
            self.assertEqual(len(voice.play_calls), 3)

            # Next track inherits guild playback options from offset 0.0.
            now = 120.0
            await voice.finish_track(1)
            self.assertEqual(len(voice.play_calls), 4)
            self.assertIn("slug=2", voice.play_calls[3][1])
            self.assertEqual(voice.play_calls[3][2], updated2)
            self.assertEqual(voice.play_calls[3][3], 0.0)

            # Stop resets speed & preserve_pitch to defaults.
            await player.stop(1, 10)
            self.assertEqual(player.get_queue(1).playback, DEFAULT_PLAYBACK_OPTIONS)

        await player.aclose()

    async def test_set_speed_while_paused_restarts_on_resume_at_paused_offset(
        self,
    ) -> None:
        voice = FakeVoiceManager()
        backend = FakeAudioBackend()
        player = MusicPlayerService(voice, backend, stream_probe=FakeStreamProbe())

        now = 50.0
        with patch(
            "milihu.usecases.music_service.time.monotonic", side_effect=lambda: now
        ):
            await player.enqueue(1, 10, "https://example.com/1", requested_by=1)
            now = 62.5
            await player.pause(1, 10)
            self.assertTrue(voice.is_paused(1))

            # Changing speed while paused does not spawn a new stream yet.
            now = 100.0
            updated = await player.set_speed(1, 10, speed=0.75, preserve_pitch=False)
            self.assertEqual(
                updated, PlaybackOptions(speed=0.75, preserve_pitch=False)
            )
            self.assertEqual(len(voice.play_calls), 1)
            self.assertTrue(voice.is_paused(1))

            # Resuming restarts from the exact paused media position (12.5s).
            now = 120.0
            resumed = await player.resume(1, 10)
            self.assertEqual(resumed.title, "1")
            self.assertEqual(len(voice.play_calls), 2)
            self.assertEqual(voice.play_calls[1][2], updated)
            self.assertAlmostEqual(voice.play_calls[1][3], 12.5)
            self.assertEqual(
                voice.play_calls[1][4], (("Authorization", "nl-token"),)
            )
            self.assertTrue(voice.is_playing(1))

        await player.aclose()

    async def test_set_speed_validation_and_live_stream_blocking(self) -> None:
        voice = FakeVoiceManager()
        probe = FakeStreamProbe()
        probe.probe_results["https://radio.example/live"] = ProbedAudio(
            stream_url="https://radio.example/live",
            title="Live Radio",
            is_live=True,
        )
        probe.probe_results["https://example.com/1"] = ProbedAudio(
            stream_url="https://example.com/1",
            title="Track 1",
            is_live=False,
        )
        player = MusicPlayerService(voice, None, stream_probe=probe)

        await player.enqueue(1, 10, "https://example.com/1", requested_by=1)

        # Out-of-range and non-finite speeds raise VoiceError.
        for bad_speed in (0.1, 0.0, -1.0, 5.0, float("nan"), float("inf")):
            with self.assertRaisesRegex(VoiceError, "Speed must be between"):
                await player.set_speed(1, 10, speed=bad_speed)

        # Wrong channel rejected.
        with self.assertRaisesRegex(VoiceError, "same voice channel"):
            await player.set_speed(1, 99, speed=1.25)

        # Toggling preserve_pitch at 1.0x updates state without restarting FFmpeg.
        opts = await player.set_speed(1, 10, preserve_pitch=False)
        self.assertEqual(opts, PlaybackOptions(speed=1.0, preserve_pitch=False))
        self.assertEqual(len(voice.play_calls), 1)

        # Queue a live stream while guild speed is 2.0x -> live stream plays at 1.0x
        # and changing speed during the live stream is blocked.
        await player.set_speed(1, 10, speed=2.0)
        await player.enqueue(1, 10, "https://radio.example/live", requested_by=1)
        await player.skip(1, 10)
        self.assertEqual(voice.play_calls[-1][1], "https://radio.example/live")
        self.assertEqual(voice.play_calls[-1][2], DEFAULT_PLAYBACK_OPTIONS)
        self.assertEqual(voice.play_calls[-1][3], 0.0)

        with self.assertRaisesRegex(VoiceError, "live streams"):
            await player.set_speed(1, 10, speed=1.5)

        await player.aclose()

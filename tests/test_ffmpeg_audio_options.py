import math
import unittest

from pydantic import ValidationError

from milihu.services.ffmpeg.audio_options import (
    build_audio_filter,
    build_ffmpeg_options,
)
from milihu.types.default.voice import (
    DEFAULT_FFMPEG_BEFORE_OPTIONS,
    DEFAULT_FFMPEG_OPTIONS,
    DEFAULT_PLAYBACK_OPTIONS,
)
from milihu.types.voice import PlaybackOptions


class FfmpegAudioOptionsTests(unittest.TestCase):
    def test_playback_options_validation(self) -> None:
        self.assertEqual(DEFAULT_PLAYBACK_OPTIONS.speed, 1.0)
        self.assertTrue(DEFAULT_PLAYBACK_OPTIONS.preserve_pitch)
        self.assertTrue(DEFAULT_PLAYBACK_OPTIONS.is_normal_speed)

        for invalid in (0.0, -1.0, 0.24, 4.01, math.nan, math.inf, -math.inf):
            with self.assertRaises(ValidationError):
                PlaybackOptions(speed=invalid)

    def test_build_audio_filter_normal_speed_returns_none(self) -> None:
        self.assertIsNone(build_audio_filter(PlaybackOptions(speed=1.0)))
        self.assertIsNone(
            build_audio_filter(PlaybackOptions(speed=1.0, preserve_pitch=False))
        )

    def test_build_audio_filter_preserve_pitch_chains_atempo(self) -> None:
        cases = {
            0.25: "atempo=0.5,atempo=0.5",
            0.4: "atempo=0.5,atempo=0.8",
            0.5: "atempo=0.5",
            1.5: "atempo=1.5",
            2.0: "atempo=2",
            3.0: "atempo=2.0,atempo=1.5",
            4.0: "atempo=2.0,atempo=2",
        }
        for speed, expected in cases.items():
            with self.subTest(speed=speed):
                self.assertEqual(
                    build_audio_filter(
                        PlaybackOptions(speed=speed, preserve_pitch=True)
                    ),
                    expected,
                )

    def test_build_audio_filter_pitch_shifted_uses_asetrate(self) -> None:
        cases = {
            0.25: "aresample=48000,asetrate=12000,aresample=48000",
            0.5: "aresample=48000,asetrate=24000,aresample=48000",
            1.25: "aresample=48000,asetrate=60000,aresample=48000",
            2.0: "aresample=48000,asetrate=96000,aresample=48000",
            4.0: "aresample=48000,asetrate=192000,aresample=48000",
        }
        for speed, expected in cases.items():
            with self.subTest(speed=speed):
                self.assertEqual(
                    build_audio_filter(
                        PlaybackOptions(speed=speed, preserve_pitch=False)
                    ),
                    expected,
                )

    def test_build_audio_filter_applies_atrim_before_speed_filter(self) -> None:
        self.assertEqual(
            build_audio_filter(DEFAULT_PLAYBACK_OPTIONS, seek_seconds=12.5),
            "atrim=start=12.500,asetpts=PTS-STARTPTS",
        )
        self.assertEqual(
            build_audio_filter(
                PlaybackOptions(speed=1.5, preserve_pitch=True), seek_seconds=12.5
            ),
            "atrim=start=12.500,asetpts=PTS-STARTPTS,atempo=1.5",
        )
        self.assertEqual(
            build_audio_filter(
                PlaybackOptions(speed=1.5, preserve_pitch=False), seek_seconds=12.5
            ),
            "atrim=start=12.500,asetpts=PTS-STARTPTS,"
            "aresample=48000,asetrate=72000,aresample=48000",
        )

    def test_build_ffmpeg_options(self) -> None:
        self.assertIn(
            "-protocol_whitelist http,https,tcp,tls,crypto",
            DEFAULT_FFMPEG_BEFORE_OPTIONS,
        )
        self.assertNotIn("-ss", DEFAULT_FFMPEG_BEFORE_OPTIONS)
        self.assertEqual(build_ffmpeg_options(), DEFAULT_FFMPEG_OPTIONS)
        self.assertEqual(
            build_ffmpeg_options(PlaybackOptions(speed=1.5, preserve_pitch=True)),
            f"{DEFAULT_FFMPEG_OPTIONS} -af atempo=1.5",
        )
        self.assertEqual(
            build_ffmpeg_options(
                PlaybackOptions(speed=1.5, preserve_pitch=False), seek_seconds=4.25
            ),
            f"{DEFAULT_FFMPEG_OPTIONS} "
            "-af atrim=start=4.250,asetpts=PTS-STARTPTS,"
            "aresample=48000,asetrate=72000,aresample=48000",
        )

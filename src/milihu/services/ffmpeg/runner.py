"""Low-level FFmpeg and ffprobe subprocess runner."""

import asyncio
import json
from pathlib import Path
from typing import cast


class FfmpegRunner:
    """Executes ``ffmpeg`` and ``ffprobe`` subprocesses."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self.executable = executable

    async def run(self, *args: str) -> None:
        """Execute ``ffmpeg`` with ``args`` and raise ``RuntimeError`` on failure."""
        process = await asyncio.create_subprocess_exec(
            self.executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip())

    def _ffprobe_executable(self) -> str:
        if "/" not in self.executable and "\\" not in self.executable:
            return "ffprobe"
        return str(Path(self.executable).with_name("ffprobe"))

    async def _run_ffprobe(self, *args: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                self._ffprobe_executable(),
                "-v",
                "error",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return None
        if process.returncode != 0:
            return None
        return stdout.decode(errors="replace").strip()

    async def probe_duration(self, input_path: Path) -> float | None:
        """Return media duration in seconds via ``ffprobe``, or ``None`` if unknown."""
        raw = await self._run_ffprobe(
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(input_path),
        )
        if raw is None:
            return None
        try:
            duration = float(raw)
        except ValueError:
            return None
        return duration if duration > 0 else None

    async def probe_width(self, input_path: Path) -> int | None:
        """Return display pixel width of the first video stream, or ``None``."""
        raw = await self._run_ffprobe(
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:stream_side_data=rotation",
            "-of",
            "json",
            str(input_path),
        )
        if raw is None:
            return None
        try:
            payload = cast(dict[str, object], json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return None
        streams = payload.get("streams")
        if not isinstance(streams, list) or not streams:
            return None
        stream_list = cast(list[object], streams)
        stream = stream_list[0]
        if not isinstance(stream, dict):
            return None
        stream_dict = cast(dict[str, object], stream)
        width = stream_dict.get("width")
        height = stream_dict.get("height")
        if not isinstance(width, int) or width <= 0:
            return None
        side_data = stream_dict.get("side_data_list")
        if isinstance(side_data, list):
            for entry in cast(list[object], side_data):
                if not isinstance(entry, dict):
                    continue
                rotation = cast(dict[str, object], entry).get("rotation")
                if (
                    isinstance(rotation, (int, float))
                    and not isinstance(rotation, bool)
                    and abs(round(rotation)) % 180 == 90
                    and isinstance(height, int)
                    and height > 0
                ):
                    return height
        return width

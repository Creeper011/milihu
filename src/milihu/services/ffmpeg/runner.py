"""Low-level FFmpeg and ffprobe subprocess runner."""

import asyncio
from pathlib import Path


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

    async def probe_duration(self, input_path: Path) -> float | None:
        """Return media duration in seconds via ``ffprobe``, or ``None`` if unknown."""
        try:
            process = await asyncio.create_subprocess_exec(
                self._ffprobe_executable(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(input_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
        except OSError:
            return None
        if process.returncode != 0:
            return None
        try:
            duration = float(stdout.decode().strip())
        except ValueError:
            return None
        return duration if duration > 0 else None

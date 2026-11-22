"""Low-level ffmpeg process execution."""

import asyncio


class FfmpegRunner:
    """Executes ``ffmpeg`` subprocesses."""

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

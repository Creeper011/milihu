"""Generic progressive preset fitting for byte-budgeted payloads."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any

from milihu.exceptions.payload import PayloadTooLargeError


class PayloadFitter:
    """Fit generated payloads within a target byte budget using binary search."""

    @staticmethod
    async def render_within_budget(
        run_render: Callable[[], Coroutine[Any, Any, None]],
        output_path: Path,
        max_bytes: int | None,
        *,
        poll_seconds: float = 0.2,
    ) -> bytes | None:
        """Run ``run_render`` and abort early if ``output_path`` passes budget."""
        output_path.unlink(missing_ok=True)
        task = asyncio.create_task(run_render())
        try:
            if max_bytes is not None:
                while not task.done():
                    await asyncio.wait({task}, timeout=poll_seconds)
                    if task.done():
                        break
                    try:
                        current_size = output_path.stat().st_size
                    except FileNotFoundError:
                        continue
                    if current_size > max_bytes:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                        return None

            await task
        except BaseException:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise

        return output_path.read_bytes()

    async def fit[PresetT](
        self,
        presets: Iterable[PresetT],
        render: Callable[[PresetT], Awaitable[bytes | None]],
        max_bytes: int | None,
        *,
        prefer_first: bool = True,
        check_last_first: bool = False,
    ) -> bytes:
        """Fit ``presets`` within ``max_bytes`` using ``render``.

        ``render`` returns generated ``bytes`` or ``None`` when aborted early for
        exceeding ``max_bytes``. When ``max_bytes`` is ``None``, only the first
        preset is rendered. Raises ``ValueError`` if ``presets`` is empty, or
        ``PayloadTooLargeError`` if every preset exceeds ``max_bytes``.
        """
        items = list(presets)
        if not items:
            raise ValueError("At least one preset is required.")

        if max_bytes is None:
            result = await render(items[0])
            if result is None:
                raise ValueError("Early byte-budget abort requires max_bytes.")
            return result

        async def attempt(preset: PresetT) -> bytes | None:
            result = await render(preset)
            if result is None or len(result) > max_bytes:
                return None
            return result

        lo, hi = 0, len(items) - 1
        best_bytes: bytes | None = None

        if check_last_first and len(items) > 1:
            data = await attempt(items[-1])
            if data is None:
                raise PayloadTooLargeError(max_bytes)
            best_bytes = data
            hi = len(items) - 2
        elif prefer_first:
            data = await attempt(items[0])
            if data is not None:
                return data
            lo = 1

        while lo <= hi:
            mid = (lo + hi) // 2
            data = await attempt(items[mid])
            if data is not None:
                best_bytes = data
                hi = mid - 1
            else:
                lo = mid + 1

        if best_bytes is not None:
            return best_bytes
        raise PayloadTooLargeError(max_bytes)

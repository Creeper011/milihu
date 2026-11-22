"""Generic progressive preset fitting for byte-budgeted payloads."""

from collections.abc import Awaitable, Callable, Iterable
from typing import Self

from milihu.exceptions.payload import PayloadTooLargeError


class PayloadFitter:
    """Singleton helper that fits generated payloads within a target byte budget."""

    _instance: Self | None = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

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

"""Cog discovery utilities."""

from collections.abc import Iterator
from pathlib import Path

from milihu.types.cogpath import CogPath


class CogFinder:
    """Recursively discovers Python cog modules under a directory."""

    def __init__(
        self,
        cogs_dir: Path | str = "cogs",
        *,
        package: str | None = None,
    ) -> None:
        self.cogs_dir = Path(cogs_dir)
        self.package = package

    def find(self) -> list[CogPath]:
        """Return sorted dotted module paths for all cogs found."""
        return list(self)

    def __iter__(self) -> Iterator[CogPath]:
        if not self.cogs_dir.is_dir():
            return

        prefix = f"{self.package}." if self.package else f"{self.cogs_dir.name}."

        for path in sorted(self.cogs_dir.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            relative = path.relative_to(self.cogs_dir).with_suffix("")
            module = ".".join(relative.parts)
            yield f"{prefix}{module}"

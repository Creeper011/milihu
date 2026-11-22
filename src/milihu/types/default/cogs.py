"""Default cog discovery constants."""

from pathlib import Path

from milihu.types.cogpath import CogPath

DEFAULT_COGS_DIR: Path = Path(__file__).resolve().parents[2] / "discord" / "cogs"
DEFAULT_COGS_PACKAGE: CogPath = "milihu.discord.cogs"

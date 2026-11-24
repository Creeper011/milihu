"""Default bot presentation, URL extraction, and cog discovery constants."""

import re
from pathlib import Path

from milihu.types.cogpath import CogPath

DEFAULT_COGS_DIR: Path = Path(__file__).resolve().parents[2] / "discord" / "cogs"
DEFAULT_COGS_PACKAGE: CogPath = "milihu.discord.cogs"

MOBILE_BROWSER = "Discord iOS"

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
URL_TRAILING_PUNCT = ".,!?;:)'\""

COLOR_PLAYING = 0x5865F2
COLOR_PAUSED = 0xFEE75C
COLOR_LIVE = 0xED4245

ACCENT_COLOUR = 0x5865F2

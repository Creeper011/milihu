"""Default environment file location."""

from pathlib import Path

DEFAULT_ENV_PATH: Path = Path(__file__).resolve().parents[4] / "static" / ".env"

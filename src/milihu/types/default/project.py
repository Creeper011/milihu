"""Default constants for project metadata and GitHub remote resolution."""

import re
from pathlib import Path
from types import MappingProxyType

from pydantic import PositiveFloat

from milihu.types.common import NonEmptyStr

CREATOR_USER_ID: int = 713544201828040786

GITHUB_REMOTE_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
GITHUB_API_REPOS_URL: NonEmptyStr = "https://api.github.com/repos"
GITHUB_WEB_BASE_URL: NonEmptyStr = "https://github.com"
GITHUB_API_HEADERS: MappingProxyType[str, str] = MappingProxyType(
    {
        "Accept": "application/vnd.github+json",
        "User-Agent": "milihu",
    }
)

DEFAULT_REPO_DIR: Path = Path(__file__).resolve().parents[4]
DEFAULT_GIT_REMOTE: NonEmptyStr = "origin"
DEFAULT_PROJECT_DESCRIPTION: NonEmptyStr = (
    "A random utility bot for Discord | Music, Download, Image"
)
DEFAULT_GITHUB_TIMEOUT_SECONDS: PositiveFloat = 5.0
DEFAULT_PROJECT_CACHE_TTL_SECONDS: PositiveFloat = 3600.0
DEFAULT_PROJECT_RETRY_TTL_SECONDS: PositiveFloat = 300.0

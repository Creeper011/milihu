"""YouTube search type definitions."""

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.common import NonEmptyStr

type SearchCandidates = tuple[HttpUrl, ...]


class YouTubeClientInfo(BaseModel):
    """InnerTube client metadata sent in YouTube search requests."""

    model_config = ConfigDict(frozen=True)

    clientName: NonEmptyStr
    clientVersion: NonEmptyStr
    hl: NonEmptyStr
    gl: NonEmptyStr


class YouTubeClientContext(BaseModel):
    """InnerTube request context wrapper."""

    model_config = ConfigDict(frozen=True)

    client: YouTubeClientInfo


class YouTubeSearchEndpoint(BaseModel):
    """Endpoint configuration for a YouTube InnerTube search backend."""

    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    context: YouTubeClientContext
    headers: tuple[tuple[NonEmptyStr, NonEmptyStr], ...] = ()

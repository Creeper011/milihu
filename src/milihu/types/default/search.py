"""Default settings and constants for YouTube search resolution."""

from pydantic import HttpUrl, PositiveFloat, PositiveInt, TypeAdapter

from milihu.types.search import (
    YouTubeClientContext,
    YouTubeClientInfo,
    YouTubeSearchEndpoint,
)

_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

YOUTUBE_MUSIC_SEARCH_ENDPOINT = YouTubeSearchEndpoint(
    url=_URL_ADAPTER.validate_python("https://music.youtube.com/youtubei/v1/search"),
    context=YouTubeClientContext(
        client=YouTubeClientInfo(
            clientName="WEB_REMIX",
            clientVersion="1.20250101.01.00",
            hl="en",
            gl="US",
        )
    ),
    headers=(
        ("Origin", "https://music.youtube.com"),
        ("Referer", "https://music.youtube.com/"),
    ),
)

YOUTUBE_WEB_SEARCH_ENDPOINT = YouTubeSearchEndpoint(
    url=_URL_ADAPTER.validate_python("https://www.youtube.com/youtubei/v1/search"),
    context=YouTubeClientContext(
        client=YouTubeClientInfo(
            clientName="WEB",
            clientVersion="2.20250101.00.00",
            hl="en",
            gl="US",
        )
    ),
)

DEFAULT_MAX_SEARCH_RESULTS: PositiveInt = 5
DEFAULT_SEARCH_TIMEOUT_SECONDS: PositiveFloat = 10.0

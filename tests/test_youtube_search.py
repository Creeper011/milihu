import unittest
from collections.abc import Mapping
from typing import Any, Self
from unittest.mock import patch

from aiohttp import ClientError

from milihu.exceptions.voice import VoiceError
from milihu.services.youtube_search import YouTubeSearchResolver
from milihu.types.default.search import (
    YOUTUBE_MUSIC_SEARCH_ENDPOINT,
    YOUTUBE_WEB_SEARCH_ENDPOINT,
)


class FakeResponse:
    def __init__(self, data: Any, error: Exception | None = None) -> None:
        self.data = data
        self.error = error

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    async def json(self) -> Any:
        return self.data


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[
            tuple[str, dict[str, Any], Mapping[str, str] | None]
        ] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> FakeResponse:
        self.requests.append((url, json, headers))
        return self._responses.pop(0)


def _music_payload(*sections: dict[str, Any]) -> dict[str, Any]:
    return {
        "contents": {
            "tabbedSearchResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "selected": True,
                            "content": {
                                "sectionListRenderer": {
                                    "contents": list(sections),
                                }
                            },
                        }
                    }
                ]
            }
        }
    }


def _web_payload(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [
                            {
                                "itemSectionRenderer": {
                                    "contents": list(items),
                                }
                            }
                        ]
                    }
                }
            }
        }
    }


class YouTubeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_prefers_youtube_music_and_deduplicates(self) -> None:
        music_data = _music_payload(
            {
                "musicCardShelfRenderer": {
                    "onTap": {"watchEndpoint": {"videoId": "TBrR-xL7lks"}},
                    "contents": [
                        {
                            "musicResponsiveListItemRenderer": {
                                "playlistItemData": {"videoId": "related_01"}
                            }
                        }
                    ],
                }
            },
            {
                "itemSectionRenderer": {
                    "contents": [
                        {
                            "musicResponsiveListItemRenderer": {
                                "playlistItemData": {"videoId": "TBrR-xL7lks"}
                            }
                        },
                        {
                            "musicResponsiveListItemRenderer": {
                                "playlistItemData": {"videoId": "bad id"}
                            }
                        },
                        {
                            "musicResponsiveListItemRenderer": {
                                "playlistItemData": {"videoId": "tz7hhl8--nM"}
                            }
                        },
                        {
                            "musicResponsiveListItemRenderer": {
                                "flexColumns": [
                                    {
                                        "musicResponsiveListItemFlexColumnRenderer": {
                                            "text": {
                                                "runs": [{"text": "Artist Name"}]
                                            }
                                        }
                                    }
                                ]
                            }
                        },
                        {
                            "musicResponsiveListItemRenderer": {
                                "overlay": {
                                    "musicItemThumbnailOverlayRenderer": {
                                        "content": {
                                            "musicPlayButtonRenderer": {
                                                "playNavigationEndpoint": {
                                                    "watchEndpoint": {
                                                        "videoId": "RJpjG90JnsM"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                    ]
                }
            },
        )
        session = FakeSession([FakeResponse(music_data)])
        with patch(
            "milihu.services.youtube_search.ClientSession", return_value=session
        ):
            urls = await YouTubeSearchResolver(max_results=3).search(
                "Meant To Be - cuntsniffer"
            )

        self.assertEqual(
            [str(u) for u in urls],
            [
                "https://www.youtube.com/watch?v=TBrR-xL7lks",
                "https://www.youtube.com/watch?v=tz7hhl8--nM",
                "https://www.youtube.com/watch?v=RJpjG90JnsM",
            ],
        )
        self.assertEqual(len(session.requests), 1)
        url, body, headers = session.requests[0]
        self.assertEqual(url, str(YOUTUBE_MUSIC_SEARCH_ENDPOINT.url))
        self.assertEqual(body["query"], "Meant To Be - cuntsniffer")
        self.assertEqual(headers, dict(YOUTUBE_MUSIC_SEARCH_ENDPOINT.headers))

    async def test_music_artist_card_extracts_nested_and_shelf_songs(self) -> None:
        music_data = _music_payload(
            {
                "musicCardShelfRenderer": {
                    "title": {"runs": [{"text": "Radiohead"}]},
                    "contents": [
                        {
                            "musicResponsiveListItemRenderer": {
                                "playlistItemData": {"videoId": "FapBH3j6WoA"}
                            }
                        }
                    ],
                }
            },
            {
                "musicShelfRenderer": {
                    "contents": [
                        {
                            "musicResponsiveListItemRenderer": {
                                "flexColumns": [
                                    {
                                        "musicResponsiveListItemFlexColumnRenderer": {
                                            "text": {
                                                "runs": [
                                                    {
                                                        "text": "Creep",
                                                        "navigationEndpoint": {
                                                            "watchEndpoint": {
                                                                "videoId": "9RfVp-GhKfs"
                                                            }
                                                        },
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            },
        )
        session = FakeSession([FakeResponse(music_data)])
        with patch(
            "milihu.services.youtube_search.ClientSession", return_value=session
        ):
            urls = await YouTubeSearchResolver().search("radiohead")

        self.assertEqual(
            [str(u) for u in urls],
            [
                "https://www.youtube.com/watch?v=FapBH3j6WoA",
                "https://www.youtube.com/watch?v=9RfVp-GhKfs",
            ],
        )

    async def test_search_falls_back_to_web_when_music_empty_or_fails(self) -> None:
        web_data = _web_payload(
            {"channelRenderer": {"channelId": "channel"}},
            {"videoRenderer": {"videoId": "bad id"}},
            {
                "videoRenderer": {
                    "videoId": "upcoming123",
                    "upcomingEventData": {"startTime": "1900000000"},
                }
            },
            {"videoRenderer": {"videoId": "abc_123"}},
            {
                "shelfRenderer": {
                    "content": {
                        "verticalListRenderer": {
                            "items": [
                                {"videoRenderer": {"videoId": "abc_123"}},
                                {"videoRenderer": {"videoId": "def_456"}},
                            ]
                        }
                    }
                }
            },
        )
        for music_response in (
            FakeResponse(_music_payload()),
            FakeResponse({}, ClientError("music down")),
        ):
            session = FakeSession([music_response, FakeResponse(web_data)])
            with patch(
                "milihu.services.youtube_search.ClientSession", return_value=session
            ):
                urls = await YouTubeSearchResolver().search("some video")

            self.assertEqual(
                [str(u) for u in urls],
                [
                    "https://www.youtube.com/watch?v=abc_123",
                    "https://www.youtube.com/watch?v=def_456",
                ],
            )
            self.assertEqual(
                [req[0] for req in session.requests],
                [
                    str(YOUTUBE_MUSIC_SEARCH_ENDPOINT.url),
                    str(YOUTUBE_WEB_SEARCH_ENDPOINT.url),
                ],
            )
            self.assertEqual(session.requests[1][1]["query"], "some video")

    async def test_search_reports_age_restriction_empty_and_network_errors(
        self,
    ) -> None:
        age_gate_payload = _web_payload(
            {
                "backgroundPromoRenderer": {
                    "title": {"runs": [{"text": "Confirm your age"}]},
                    "icon": {"iconType": "EMPTY_SEARCH"},
                    "ctaButton": {"buttonRenderer": {"text": "Sign in"}},
                }
            }
        )
        cases = (
            (
                [FakeResponse(_music_payload()), FakeResponse(age_gate_payload)],
                "restricted this search",
            ),
            (
                [FakeResponse(_music_payload()), FakeResponse({"contents": {}})],
                "No videos found",
            ),
            (
                [
                    FakeResponse({}, ClientError("music failed")),
                    FakeResponse({}, ClientError("web failed")),
                ],
                "Could not search YouTube right now",
            ),
        )
        for responses, pattern in cases:
            with (
                patch(
                    "milihu.services.youtube_search.ClientSession",
                    return_value=FakeSession(responses),
                ),
                self.assertRaisesRegex(VoiceError, pattern),
            ):
                await YouTubeSearchResolver().search("missing")

    def test_invalid_max_results_rejected(self) -> None:
        with self.assertRaises(ValueError):
            YouTubeSearchResolver(max_results=0)

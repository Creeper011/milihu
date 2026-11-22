"""Search contract for resolving text to playable source URLs."""

from typing import Protocol

from milihu.types.search import SearchCandidates


class SearchResolver(Protocol):
    async def search(self, query: str) -> SearchCandidates: ...

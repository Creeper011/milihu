"""Default NodeLink runtime and response constants."""

GENERIC_AUTHORS: frozenset[str] = frozenset({"unknown", "unknown artist"})
OUTAGE_MARKERS: tuple[str, ...] = (
    "runtime contract is incomplete",
    "source manager is not available",
    "sources manager is not available",
)

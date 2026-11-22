"""Temporary upload lifetime definitions."""

from enum import StrEnum


class UploadLifetime(StrEnum):
    ONE_HOUR = "1h"
    TWELVE_HOURS = "12h"
    TWENTY_FOUR_HOURS = "24h"
    SEVENTY_TWO_HOURS = "72h"

"""Pure-domain help category type definitions."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from milihu.types.common import NonEmptyStr

type HelpHomePage = Literal["home"]


class HelpCategoryKey(StrEnum):
    """Identifiers for public help command categories."""

    MEDIA = "media"
    MUSIC = "music"
    UTILITY = "utility"


type HelpPage = HelpHomePage | HelpCategoryKey


class HelpCategory[ItemT](BaseModel):
    """Display metadata and member references for a help category."""

    model_config = ConfigDict(frozen=True)

    label: NonEmptyStr
    emoji: NonEmptyStr
    items: tuple[ItemT, ...] = Field(min_length=1)

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.label}"


type HelpCategories[ItemT] = Mapping[HelpCategoryKey, HelpCategory[ItemT]]

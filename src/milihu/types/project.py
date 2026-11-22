"""Project metadata output type definitions."""

from pydantic import BaseModel, ConfigDict, HttpUrl

from milihu.types.common import NonEmptyStr


class RemoteProjectInfo(BaseModel):
    """Metadata resolved from the project's git/GitHub remote."""

    model_config = ConfigDict(frozen=True)

    description: NonEmptyStr | None = None
    repository_url: HttpUrl | None = None


class ProjectOutput(BaseModel):
    """Project metadata returned to presentation layers."""

    model_config = ConfigDict(frozen=True)

    description: NonEmptyStr | None = None
    repository_url: HttpUrl | None = None
    stack: tuple[NonEmptyStr, ...]

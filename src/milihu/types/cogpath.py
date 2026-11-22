"""Cog path type definitions."""

from typing import Annotated

from pydantic import StringConstraints

CogPath = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$",
    ),
]

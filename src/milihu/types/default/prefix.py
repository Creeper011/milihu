"""Prefix type definitions."""

from typing import Annotated

from pydantic import StringConstraints

Prefix = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

DEFAULT_PREFIX: Prefix = "m$"

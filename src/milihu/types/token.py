"""Token type definitions."""

from typing import Annotated

from pydantic import StringConstraints

Token = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

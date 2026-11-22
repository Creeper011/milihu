"""Code input pattern for the evaluation command."""

import re

FENCED_PYTHON_CODE = re.compile(
    r"^```(?:python|py)?\n(?P<code>[\s\S]*?)\n?```$", re.I
)

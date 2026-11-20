"""Text normalization shared by duplicate detection, matching, and GL coding."""

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize(value: str | None) -> str:
    """Trim, lowercase, and strip punctuation and spaces: ' NWO-2001 ' -> 'nwo2001'."""
    return _NON_ALNUM.sub("", (value or "").lower())

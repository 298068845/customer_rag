from __future__ import annotations

import re
from datetime import datetime


DISPLAY_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def display_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime(DISPLAY_DATETIME_FORMAT)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime(DISPLAY_DATETIME_FORMAT)
        except ValueError:
            pass
    return text


def now_display() -> str:
    return datetime.now().strftime(DISPLAY_DATETIME_FORMAT)


_ISO_DATETIME_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


def display_datetimes_in_text(text: object) -> str:
    value = str(text or "")
    return _ISO_DATETIME_PATTERN.sub(lambda match: display_datetime(match.group(0)), value)

from datetime import datetime
from typing import Iterable


def in_any_window(now: datetime, windows: Iterable[str]) -> bool:
    minutes = now.hour * 60 + now.minute
    for window in windows:
        start, end = parse_window(window)
        if start <= minutes <= end:
            return True
    return False


def parse_window(window: str) -> tuple[int, int]:
    start_text, end_text = window.split("-", 1)
    return _hhmm_to_minutes(start_text), _hhmm_to_minutes(end_text)


def _hhmm_to_minutes(value: str) -> int:
    hour_text, minute_text = value.strip().split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"非法时间: {value}")
    return hour * 60 + minute

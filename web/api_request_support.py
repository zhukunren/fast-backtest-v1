from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import math
from typing import Any


def parse_bounded_int(
    raw: object,
    *,
    label: str,
    minimum: int = 1,
    maximum: int = 200,
) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    return min(max(value, minimum), maximum)


def normalize_client_log_event(data: object) -> tuple[str, str, str, str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("payload must be an object")
    message = str(data.get("message") or "client event").strip()
    level = str(data.get("level") or "INFO").strip().upper()
    event = str(data.get("event") or "").strip()
    page = str(data.get("page") or "").strip()
    fields = data.get("fields")
    return message, level, event, page, fields if isinstance(fields, dict) else {}


def parse_compare_names(
    raw: object,
    *,
    valid_name: Callable[[object], bool],
) -> list[str]:
    names = [name.strip() for name in str(raw or "").split(",") if name.strip()]
    if len(names) < 2:
        raise ValueError("need 2-4 unique strategy names")
    if len(names) > 4:
        raise ValueError("need 2-4 unique strategy names")
    for name in names:
        if not valid_name(name):
            raise ValueError(f"invalid name: {name}")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("strategy names must be unique")
    return names


def _canonical_compare_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    compact = text.replace("/", "-")
    if len(compact) == 8 and compact.isdigit():
        compact = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    try:
        return datetime.fromisoformat(compact.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def normalize_compare_curve(payload: dict[str, Any]) -> dict[str, float] | None:
    dates = payload.get("dates")
    values = payload.get("net_value")
    if not isinstance(dates, list) or not isinstance(values, list):
        return None
    if len(dates) < 2 or len(dates) != len(values):
        return None

    points: dict[str, float] = {}
    for raw_date, raw_value in zip(dates, values, strict=True):
        canonical_date = _canonical_compare_date(raw_date)
        if canonical_date is None:
            return None
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        # Keep the final record for a duplicated date, matching cache readers.
        points[canonical_date] = number
    if len(points) < 2:
        return None
    return dict(sorted(points.items()))


__all__ = [
    "normalize_client_log_event",
    "normalize_compare_curve",
    "parse_bounded_int",
    "parse_compare_names",
]

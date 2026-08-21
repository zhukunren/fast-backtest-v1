from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any


class BacktestRequestError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


def normalize_backtest_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise ValueError(f"invalid date: {text}")


def _normalize_names(
    raw_names: object,
    *,
    valid_name: Callable[[object], bool],
    known_names: Callable[[], set[str]],
) -> tuple[list[str], set[str]]:
    if not isinstance(raw_names, list) or not raw_names:
        raise BacktestRequestError("names must be a non-empty list")
    for name in raw_names:
        if not valid_name(name):
            raise BacktestRequestError(f"invalid name: {name}")
    available = known_names()
    for name in raw_names:
        if name not in available:
            raise BacktestRequestError(f"unknown strategy: {name}", status_code=404)
    names = list(dict.fromkeys(raw_names))
    return names, available


def _normalize_one_override(
    name: object,
    payload: object,
    *,
    selected_names: set[str],
    available_names: set[str],
    valid_name: Callable[[object], bool],
    strategy_config: Callable[[str], Mapping[str, Any]],
) -> dict[str, str]:
    if not valid_name(name):
        raise BacktestRequestError(f"invalid name: {name}")
    if name not in available_names:
        raise BacktestRequestError(f"unknown strategy: {name}", status_code=404)
    if name not in selected_names:
        raise BacktestRequestError(f"date override is not selected: {name}")
    if not isinstance(payload, dict):
        raise BacktestRequestError(f"date_overrides[{name}] must be an object")
    try:
        start_time = normalize_backtest_date(payload.get("start_time"))
        end_time = normalize_backtest_date(payload.get("end_time"))
    except ValueError as exc:
        raise BacktestRequestError(str(exc)) from exc

    config = strategy_config(str(name))
    configured_start = normalize_backtest_date(config.get("backtest.start_time"))
    configured_end = normalize_backtest_date(config.get("backtest.end_time"))
    effective_start = start_time or configured_start
    effective_end = end_time or configured_end
    if effective_start and effective_end and effective_start > effective_end:
        raise BacktestRequestError(f"invalid date range for {name}")
    override: dict[str, str] = {}
    # The catalog dialog pre-fills the strategy's configured window. Treating
    # those values as an explicit override turns a normal run into an ad-hoc
    # generation, which is intentionally not activated as the current result.
    if start_time and start_time != configured_start:
        override["start_time"] = start_time
    if end_time and end_time != configured_end:
        override["end_time"] = end_time
    return override


def parse_backtest_submission(
    data: Mapping[str, Any],
    *,
    valid_name: Callable[[object], bool],
    known_names: Callable[[], set[str]],
    strategy_config: Callable[[str], Mapping[str, Any]],
) -> tuple[list[str], dict[str, dict[str, str]] | None]:
    names, available_names = _normalize_names(
        data.get("names"),
        valid_name=valid_name,
        known_names=known_names,
    )
    raw_overrides = data.get("date_overrides")
    if raw_overrides is None:
        return names, None
    if not isinstance(raw_overrides, dict):
        raise BacktestRequestError("date_overrides must be an object")

    selected_names = set(names)
    overrides: dict[str, dict[str, str]] = {}
    for name, payload in raw_overrides.items():
        override = _normalize_one_override(
            name,
            payload,
            selected_names=selected_names,
            available_names=available_names,
            valid_name=valid_name,
            strategy_config=strategy_config,
        )
        if override:
            overrides[str(name)] = override
    return names, overrides


__all__ = [
    "BacktestRequestError",
    "normalize_backtest_date",
    "parse_backtest_submission",
]

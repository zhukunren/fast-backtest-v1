"""Framework adapter for fixed target-weight rebalancing backtests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd

from core.exceptions import DataValidationError

from .decisions import (
    DecisionIdentity,
    TargetWeightDecision,
    TargetWeightDecisionArtifacts,
)
from backtest.panel_native import PricePanel, QuoteStore
from backtest.static_allocation_contracts import validate_explicit_trade_days


def _coerce_weight_mapping(payload: Mapping[object, object]) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw_code, raw_weight in payload.items():
        code = str(raw_code or "").strip().upper()
        if not code:
            raise DataValidationError("fixed_rebalance.target_weights contains an empty symbol.")
        try:
            out[code] = float(raw_weight)
        except Exception as exc:
            raise DataValidationError(f"fixed_rebalance weight for {code} is not numeric.") from exc
    return out


def _resolve_existing_payload_path(raw: str, *, base_dir: Path | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    candidates = [path]
    if base_dir is not None and not path.is_absolute():
        candidates.append(Path(base_dir) / path)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _parse_weight_assignments(text: str) -> dict[str, float]:
    parts = [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]
    if len(parts) == 1 and " " in text:
        parts = [part.strip() for part in re.split(r"\s+", text) if part.strip()]
    if not parts:
        raise DataValidationError("fixed_rebalance.target_weights is empty.")

    out: dict[str, float] = {}
    for part in parts:
        if "=" in part:
            raw_code, raw_weight = part.split("=", 1)
        elif ":" in part:
            raw_code, raw_weight = part.split(":", 1)
        else:
            raise DataValidationError(
                "fixed_rebalance.target_weights must be JSON or code=weight pairs."
            )
        code = raw_code.strip().upper()
        if not code:
            raise DataValidationError("fixed_rebalance.target_weights contains an empty symbol.")
        try:
            out[code] = float(raw_weight)
        except Exception as exc:
            raise DataValidationError(f"fixed_rebalance weight for {code} is not numeric.") from exc
    return out


def parse_fixed_rebalance_weights(raw: object, *, base_dir: Path | None = None) -> dict[str, float]:
    if isinstance(raw, Mapping):
        return _coerce_weight_mapping(raw)
    text = str(raw or "").strip()
    if not text:
        raise DataValidationError("fixed_rebalance.target_weights is required when portfolio.engine=fixed_rebalance.")

    path = _resolve_existing_payload_path(text, base_dir=base_dir)
    if path is not None:
        text = path.read_text(encoding="utf-8")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _parse_weight_assignments(text)
    if not isinstance(payload, Mapping):
        raise DataValidationError("fixed_rebalance.target_weights JSON must be an object.")
    return _coerce_weight_mapping(payload)


def parse_fixed_rebalance_dates(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return tuple(part.strip() for part in re.split(r"[,;\s]+", text) if part.strip())
    if not isinstance(payload, list):
        raise DataValidationError("fixed_rebalance.rebalance_dates JSON must be a list.")
    return tuple(str(item).strip() for item in payload if str(item).strip())


def load_fixed_rebalance_quote_store(
    codes: list[str],
    *,
    cfg,
    symbol_type: str,
    start_date: str | None,
    end_date: str | None,
) -> QuoteStore:
    from data.market_data import preload_ohlcv_frames

    requested = sorted(
        {str(code).strip().upper() for code in codes if str(code).strip()},
        key=str.casefold,
    )
    frames = preload_ohlcv_frames(
        requested,
        symbol_type=symbol_type,
        start_date=start_date,
        end_date=end_date,
        cfg=cfg,
        allow_missing=False,
        show_progress=False,
    )
    by_code = {str(code).strip().upper(): frame for code, frame in frames.items()}
    missing = [code for code in requested if code not in by_code or by_code[code] is None or by_code[code].empty]
    if missing:
        raise DataValidationError(f"fixed_rebalance missing price data: {missing}")
    normalized_frames: list[pd.DataFrame] = []
    for code in requested:
        frame = by_code[code].copy()
        if "stock_code" not in frame.columns:
            frame["stock_code"] = code
        else:
            frame["stock_code"] = code
        normalized_frames.append(frame)
    from data.market_panel import _normalize_panel_frame

    panel_frame = _normalize_panel_frame(
        pd.concat(normalized_frames, ignore_index=True)
    )
    return PricePanel.from_frame(panel_frame).quote_store()


def build_target_weight_decision(
    *,
    cfg,
    start_time: str,
    end_time: str,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | tuple[pd.Timestamp, ...] | None = None,
    logger: Any = None,
) -> TargetWeightDecision:
    fixed_cfg = getattr(cfg, "fixed_rebalance", None)
    base_dir = Path(getattr(getattr(cfg, "paths", None), "repo_root", "."))
    weights = parse_fixed_rebalance_weights(
        getattr(fixed_cfg, "target_weights", ""),
        base_dir=base_dir,
    )
    symbol_type = str(getattr(fixed_cfg, "symbol_type", "stock") or "stock").strip().lower()
    if symbol_type not in {"stock", "index", "etf"}:
        raise DataValidationError(f"fixed_rebalance.symbol_type must be stock/index/etf, got {symbol_type!r}")

    official_trade_days = validate_explicit_trade_days(
        trade_days,
        start_date=start_time,
        end_date=end_time,
        name="fixed_rebalance.trade_days",
    )

    quote_store = load_fixed_rebalance_quote_store(
        sorted(weights, key=str.casefold),
        cfg=cfg,
        symbol_type=symbol_type,
        start_date=start_time,
        end_date=end_time,
    )
    if logger is not None:
        logger.info(
            "fixed_rebalance decision built symbols=%d trade_days=%d",
            len(weights),
            len(official_trade_days),
        )
    immutable_weights = MappingProxyType(dict(weights))
    return TargetWeightDecision(
        identity=DecisionIdentity(source="config.fixed_rebalance"),
        target_weights=immutable_weights,
        symbol_type=symbol_type,
        rebalance_frequency=str(
            getattr(fixed_cfg, "rebalance_frequency", "month_start") or "month_start"
        ),
        rebalance_dates=parse_fixed_rebalance_dates(getattr(fixed_cfg, "rebalance_dates", "")),
        buy_price_col=str(getattr(fixed_cfg, "buy_price_col", "open") or "open"),
        sell_price_col=str(getattr(fixed_cfg, "sell_price_col", "open") or "open"),
        mark_price_col=str(getattr(fixed_cfg, "mark_price_col", "close") or "close"),
        normalize_weights=bool(getattr(fixed_cfg, "normalize_weights", False)),
        quote_store=quote_store,
        artifacts=TargetWeightDecisionArtifacts(target_weights=immutable_weights),
        trade_days=official_trade_days,
    )


__all__ = [
    "build_target_weight_decision",
    "load_fixed_rebalance_quote_store",
    "parse_fixed_rebalance_dates",
    "parse_fixed_rebalance_weights",
]

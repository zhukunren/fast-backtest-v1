from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from core.exceptions import ValidationError

@dataclass(frozen=True)
class ValidationSettings:
    enabled: bool
    sample_size: int


_PYDANTIC_VALIDATION_ERROR_CLS = None
_MODEL_CLS_CACHE: dict[str, object] = {}


def _get_pydantic_validation_error_cls():
    global _PYDANTIC_VALIDATION_ERROR_CLS
    if _PYDANTIC_VALIDATION_ERROR_CLS is not None:
        return _PYDANTIC_VALIDATION_ERROR_CLS
    from pydantic import ValidationError as PydanticValidationError

    _PYDANTIC_VALIDATION_ERROR_CLS = PydanticValidationError
    return PydanticValidationError


def _get_model_cls(name: str):
    cached = _MODEL_CLS_CACHE.get(name)
    if cached is not None:
        return cached
    from core import models as core_models

    model_cls = getattr(core_models, name)
    _MODEL_CLS_CACHE[name] = model_cls
    return model_cls


def clear_validation_cache() -> None:
    """Clear lazily imported validation classes for test/runtime isolation."""
    global _PYDANTIC_VALIDATION_ERROR_CLS
    _PYDANTIC_VALIDATION_ERROR_CLS = None
    _MODEL_CLS_CACHE.clear()


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    s = str(value).strip()
    if s == "":
        return default
    try:
        return int(s)
    except Exception:
        return default


def _get_settings(cfg=None) -> ValidationSettings:
    env_enable = os.environ.get("PYDANTIC_VALIDATE")
    env_sample = os.environ.get("PYDANTIC_VALIDATE_SAMPLE")

    enabled = _parse_bool(env_enable, False) if env_enable is not None else None
    sample_size = _parse_int(env_sample, 0) if env_sample is not None else None

    if cfg is not None:
        if enabled is None:
            enabled = bool(getattr(cfg.run, "pydantic_validate", False))
        if sample_size is None:
            sample_size = int(getattr(cfg.run, "pydantic_validate_sample", 0) or 0)

    if enabled is None:
        enabled = False
    if sample_size is None:
        sample_size = 0
    return ValidationSettings(enabled=enabled, sample_size=sample_size)


def _sample_frame(df: pd.DataFrame, sample_size: int) -> pd.DataFrame:
    if sample_size <= 0 or len(df) <= sample_size:
        return df
    head = sample_size // 2
    tail = sample_size - head
    if head <= 0:
        return df.tail(sample_size)
    return pd.concat([df.head(head), df.tail(tail)], axis=0)


def _validate_records(
    records: Iterable[dict],
    model_name: str,
    *,
    source: str,
) -> None:
    model_cls = _get_model_cls(model_name)
    pydantic_validation_error_cls = _get_pydantic_validation_error_cls()
    for idx, row in enumerate(records):
        try:
            model_cls.model_validate(row)
        except Exception as exc:
            if isinstance(exc, pydantic_validation_error_cls):
                raise ValidationError(
                    f"{source} pydantic validation failed at row={idx}: {exc}",
                    errors=exc.errors(),
                ) from exc
            raise


def maybe_validate_signal_frame(df: pd.DataFrame, *, source: str = "signal_frame", cfg=None) -> None:
    settings = _get_settings(cfg=cfg)
    if not settings.enabled:
        return
    sample_df = _sample_frame(df, settings.sample_size)
    _validate_records(sample_df.to_dict("records"), "SignalRecord", source=source)


def maybe_validate_price_frame(df: pd.DataFrame, *, source: str = "price_frame", cfg=None) -> None:
    settings = _get_settings(cfg=cfg)
    if not settings.enabled:
        return
    sample_df = _sample_frame(df, settings.sample_size)
    _validate_records(sample_df.to_dict("records"), "PriceRecord", source=source)


def maybe_validate_trade_records(df: pd.DataFrame, *, source: str = "trade_records", cfg=None) -> None:
    settings = _get_settings(cfg=cfg)
    if not settings.enabled:
        return
    if df is None or df.empty:
        return

    sample_df = _sample_frame(df, settings.sample_size)
    records = []
    for _, row in sample_df.iterrows():
        records.append(
            {
                "entry_date": row.get("买入日"),
                "entry_price": row.get("买入价"),
                "exit_date": row.get("卖出日"),
                "exit_price": row.get("卖出价"),
                "return_pct": row.get("盈亏", 0.0),
                "hold_days": row.get("持仓日", 0),
                "entry_reason": row.get("买入原因", ""),
                "exit_reason": row.get("卖出原因", ""),
            }
        )
    _validate_records(records, "TradeRecord", source=source)


def maybe_validate_metrics(metrics: dict, *, source: str = "backtest_metrics", cfg=None) -> None:
    settings = _get_settings(cfg=cfg)
    if not settings.enabled:
        return
    _validate_records([metrics], "BacktestMetrics", source=source)


__all__ = [
    "ValidationSettings",
    "clear_validation_cache",
    "maybe_validate_metrics",
    "maybe_validate_price_frame",
    "maybe_validate_signal_frame",
    "maybe_validate_trade_records",
]

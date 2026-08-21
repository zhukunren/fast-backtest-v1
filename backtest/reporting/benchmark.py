"""Benchmark curve enrichment helpers for net-value payloads."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from data.tushare_client import (
    create_tushare_client_from_factory,
    redact_tushare_error,
    resolve_required_tushare_token,
)

_BENCHMARK_NAME = "创业板指"
_BENCHMARK_DISPLAY_CODE = "399006.SZ"
_BENCHMARK_SOURCE_CODES: tuple[str, ...] = (
    _BENCHMARK_DISPLAY_CODE,
    "399006.SH",
)


def _benchmark_settings(*, cfg) -> tuple[str, str, tuple[str, ...]]:
    backtest_cfg = cfg.backtest
    display_code = str(getattr(backtest_cfg, "benchmark_code", _BENCHMARK_DISPLAY_CODE) or "").strip()
    if not display_code:
        display_code = _BENCHMARK_DISPLAY_CODE
    name = str(getattr(backtest_cfg, "benchmark_name", _BENCHMARK_NAME) or "").strip() or _BENCHMARK_NAME
    raw_source_codes = getattr(backtest_cfg, "benchmark_source_codes", _BENCHMARK_SOURCE_CODES)
    if isinstance(raw_source_codes, str):
        source_codes = tuple(
            item for item in (part.strip() for part in raw_source_codes.split(",")) if item
        )
    else:
        source_codes = tuple(str(item).strip() for item in (raw_source_codes or ()) if str(item).strip())
    if not source_codes:
        source_codes = (display_code,)
    if display_code not in source_codes:
        source_codes = (display_code, *source_codes)
    return name, display_code, source_codes


def _read_benchmark_daily(
    symbol_code: str,
    trade_dates: list[str],
    *,
    start_date: str,
    end_date: str,
    cfg,
) -> pd.DataFrame:
    request_df = pd.DataFrame({"trade_date": pd.to_datetime(trade_dates, errors="coerce")})
    from data.timing_readers import read_index_for_timing

    return read_index_for_timing(
        symbol_code,
        request_df,
        start_date=start_date,
        end_date=end_date,
        columns=["trade_date", "close"],
        cfg=cfg,
    )


def _read_benchmark_daily_from_tushare(
    symbol_code: str,
    *,
    start_date: str,
    end_date: str,
    cfg,
    config_loader=None,
) -> pd.DataFrame:
    token = resolve_required_tushare_token(cfg=cfg, config_loader=config_loader)

    try:
        from tushare.pro.data_pro import pro_api
    except Exception as exc:
        raise RuntimeError(f"Tushare 导入失败: {exc}") from exc

    pro = create_tushare_client_from_factory(pro_api, token)
    try:
        df = pro.index_daily(
            ts_code=str(symbol_code),
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,close",
        )
    except Exception as exc:
        message = redact_tushare_error(exc, token=token)
        raise RuntimeError(
            f"Tushare index_daily 获取失败: symbol_code={symbol_code!r} err={message}"
        ) from None

    if df is None or df.empty:
        raise FileNotFoundError(f"Tushare index_daily 返回为空: {symbol_code!r}")

    out = df.copy()
    if "trade_date" not in out.columns or "close" not in out.columns:
        raise ValueError(f"Tushare index_daily 返回列不完整: {list(out.columns)}")
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["trade_date", "close"])
    out = out.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    if out.empty:
        raise ValueError(f"Tushare index_daily 清洗后为空: {symbol_code!r}")
    return out.loc[:, ["trade_date", "close"]].copy()


def _round_curve_values(values: Any) -> list[float]:
    rounded: list[float] = []
    for value in values:
        num = float(value)
        if not math.isfinite(num):
            raise ValueError("curve contains non-finite values")
        rounded.append(round(num, 6))
    return rounded


def build_benchmark_curves_from_net_value_payload(
    payload: dict[str, Any],
    *,
    cfg,
    config_loader=None,
) -> dict[str, Any]:
    benchmark_name, benchmark_display_code, benchmark_source_codes = _benchmark_settings(cfg=cfg)
    dates = payload.get("dates")
    net_value = payload.get("net_value")
    if not isinstance(dates, list) or not isinstance(net_value, list):
        return {}
    if not dates or len(dates) != len(net_value):
        return {}

    try:
        strategy_dates = pd.Series(pd.to_datetime(dates, errors="coerce")).dt.normalize()
        strategy_values = pd.to_numeric(pd.Series(net_value), errors="coerce")
    except Exception:
        return {}

    if strategy_dates.isna().any() or strategy_values.isna().any():
        return {}

    sorted_dates = strategy_dates.sort_values(kind="mergesort").reset_index(drop=True)
    start_date = sorted_dates.iloc[0].strftime("%Y%m%d")
    end_date = sorted_dates.iloc[-1].strftime("%Y%m%d")
    request_dates = sorted_dates.dt.strftime("%Y-%m-%d").tolist()

    benchmark_df = None
    benchmark_source_code = ""
    for source_code in benchmark_source_codes:
        try:
            candidate = _read_benchmark_daily(
                source_code,
                request_dates,
                start_date=start_date,
                end_date=end_date,
                cfg=cfg,
            )
        except Exception:
            try:
                candidate = _read_benchmark_daily_from_tushare(
                    source_code,
                    start_date=start_date,
                    end_date=end_date,
                    cfg=cfg,
                    config_loader=config_loader,
                )
            except Exception:
                continue
        if candidate is None or candidate.empty:
            continue
        if "trade_date" not in candidate.columns or "close" not in candidate.columns:
            continue
        benchmark_df = candidate.copy()
        benchmark_source_code = source_code
        break

    if benchmark_df is None:
        return {}

    try:
        benchmark_df["trade_date"] = pd.to_datetime(
            benchmark_df["trade_date"],
            errors="coerce",
        ).dt.normalize()
        benchmark_df["close"] = pd.to_numeric(benchmark_df["close"], errors="coerce")
    except Exception:
        return {}

    benchmark_df = benchmark_df.dropna(subset=["trade_date", "close"])
    benchmark_df = benchmark_df.loc[benchmark_df["close"] > 0]
    benchmark_df = benchmark_df.sort_values("trade_date", kind="mergesort")
    benchmark_df = benchmark_df.drop_duplicates(subset=["trade_date"], keep="last")
    if benchmark_df.empty:
        return {}

    benchmark_close = benchmark_df.set_index("trade_date")["close"].reindex(strategy_dates).ffill()
    if benchmark_close.isna().any():
        return {}

    first_close = float(benchmark_close.iloc[0])
    if not math.isfinite(first_close) or first_close <= 0:
        return {}

    benchmark_net_value = benchmark_close / first_close
    excess_net_value = strategy_values / benchmark_net_value.to_numpy()

    try:
        return {
            "benchmark_name": benchmark_name,
            "benchmark_code": benchmark_display_code,
            "benchmark_label": f"{benchmark_name} {benchmark_display_code}",
            "benchmark_source_code": benchmark_source_code,
            "benchmark_net_value": _round_curve_values(benchmark_net_value),
            "excess_net_value": _round_curve_values(excess_net_value),
        }
    except Exception:
        return {}


__all__ = ["build_benchmark_curves_from_net_value_payload"]

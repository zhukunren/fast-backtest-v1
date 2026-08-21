"""Cached curve, trade, and holding readers for the web library."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from config import get_config
from data.tushare_client import (
    create_tushare_client_from_factory,
    redact_tushare_error,
    resolve_required_tushare_token,
)
from web.cache_generation import WebCacheSnapshot
from web.library_cache import (
    _cache_current_for_config,
    _resolve_library_cache_snapshot,
    _snapshot_artifact_path,
)
from web.library_metadata import (
    _BENCHMARK_DISPLAY_CODE,
    _BENCHMARK_NAME,
    _BENCHMARK_SOURCE_CODES,
)


def _read_benchmark_daily(
    symbol_code: str,
    trade_dates: list[str],
    *,
    start_date: str,
    end_date: str,
):
    import pandas as pd

    request_df = pd.DataFrame({
        "trade_date": pd.to_datetime(trade_dates, errors="coerce"),
    })

    try:
        from data.timing_readers import read_index_for_timing

        return read_index_for_timing(
            symbol_code,
            request_df,
            start_date=start_date,
            end_date=end_date,
            columns=["trade_date", "close"],
            cfg=get_config(reload=True),
        )
    except Exception:
        return _read_benchmark_daily_from_tushare(
            symbol_code,
            start_date=start_date,
            end_date=end_date,
        )


def _read_benchmark_daily_from_tushare(
    symbol_code: str,
    *,
    start_date: str,
    end_date: str,
):
    import pandas as pd

    token = resolve_required_tushare_token(cfg=get_config())

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


_ALIGNED_NET_VALUE_KEYS = {
    "net_value",
    "drawdown",
    "utilization",
    "benchmark_net_value",
    "excess_net_value",
}


def _validate_net_value_payload(payload: Any) -> dict[str, Any] | None:
    """Validate the current net-value artifact without repairing it."""
    if not isinstance(payload, dict):
        return None
    dates = payload.get("dates")
    raw_values = payload.get("net_value")
    if not isinstance(dates, list) or not isinstance(raw_values, list):
        return None
    if not dates or len(dates) != len(raw_values):
        return None

    parsed_dates: list[datetime] = []
    for value in dates:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
        parsed_dates.append(parsed)
    if parsed_dates != sorted(set(parsed_dates)):
        return None

    values: list[float] = []
    for value in raw_values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0:
            return None
        values.append(number)

    validated: dict[str, Any] = dict(payload)
    validated["dates"] = list(dates)
    validated["net_value"] = values

    row_count = len(dates)
    for key in _ALIGNED_NET_VALUE_KEYS - {"net_value"}:
        raw_series = payload.get(key)
        if raw_series is None:
            continue
        if not isinstance(raw_series, list) or len(raw_series) != row_count:
            return None
        converted: list[float] = []
        try:
            for value in raw_series:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError
                converted.append(number)
        except (TypeError, ValueError):
            return None
        validated[key] = converted

    portfolios = payload.get("portfolios")
    if isinstance(portfolios, dict):
        normalized_portfolios: dict[str, list[float]] = {}
        for key, raw_series in portfolios.items():
            if not isinstance(raw_series, list) or len(raw_series) != row_count:
                return None
            converted: list[float] = []
            try:
                for value in raw_series:
                    number = float(value)
                    if not math.isfinite(number) or number <= 0:
                        raise ValueError
                    converted.append(number)
            except (TypeError, ValueError):
                return None
            normalized_portfolios[str(key)] = converted
        validated["portfolios"] = normalized_portfolios
    elif portfolios is not None:
        return None

    return validated


def _build_benchmark_curves(
    payload: dict[str, Any],
) -> dict[str, Any]:
    validated = _validate_net_value_payload(payload)
    if validated is None:
        return {}
    dates = validated.get("dates")
    net_value = validated.get("net_value")
    if not isinstance(dates, list) or not isinstance(net_value, list):
        return {}
    if not dates or len(dates) != len(net_value):
        return {}

    try:
        import pandas as pd

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
    for source_code in _BENCHMARK_SOURCE_CODES:
        try:
            candidate = _read_benchmark_daily(
                source_code,
                request_dates,
                start_date=start_date,
                end_date=end_date,
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
            "benchmark_name": _BENCHMARK_NAME,
            "benchmark_code": _BENCHMARK_DISPLAY_CODE,
            "benchmark_label": f"{_BENCHMARK_NAME} {_BENCHMARK_DISPLAY_CODE}",
            "benchmark_source_code": benchmark_source_code,
            "benchmark_net_value": _round_curve_values(benchmark_net_value),
            "excess_net_value": _round_curve_values(excess_net_value),
        }
    except Exception:
        return {}


def get_library_net_value(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
    include_benchmark: bool = True,
) -> dict[str, Any] | None:
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None or not _cache_current_for_config(name, snapshot=snapshot):
        return None
    p = _snapshot_artifact_path(snapshot, "net_value.json")
    if p is None or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    payload = _validate_net_value_payload(payload)
    if payload is None:
        return None
    enriched = (
        _build_benchmark_curves(payload)
        if include_benchmark
        else {}
    )
    if enriched:
        payload = dict(payload)
        payload.update(enriched)
    return payload


def get_library_trades(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None or not _cache_current_for_config(name, snapshot=snapshot):
        return None
    p = _snapshot_artifact_path(snapshot, "trades.json")
    if p is None or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def get_library_daily_holdings(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None or not _cache_current_for_config(name, snapshot=snapshot):
        return None
    p = _snapshot_artifact_path(snapshot, "daily_holdings.json")
    if p is None or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("dates"), list):
        return None
    if not isinstance(payload.get("holdings_by_date"), dict):
        return None
    if not isinstance(payload.get("summary_by_date"), dict):
        return None
    return payload


def get_library_position_changes(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> list[dict[str, Any]] | None:
    """获取策略的仓位变化事件记录。"""
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None or not _cache_current_for_config(name, snapshot=snapshot):
        return None
    p = _snapshot_artifact_path(snapshot, "position_changes.json")
    if p is None or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    return [item for item in records if isinstance(item, dict)]


def get_library_market_state(
    name: str,
    *,
    snapshot: WebCacheSnapshot | None = None,
) -> dict[str, Any] | None:
    snapshot = snapshot or _resolve_library_cache_snapshot(name)
    if snapshot is None or not _cache_current_for_config(name, snapshot=snapshot):
        return None
    p = _snapshot_artifact_path(snapshot, "market_state.json")
    if p is None or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# 回测执行 + 缓存
# ---------------------------------------------------------------------------

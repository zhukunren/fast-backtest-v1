"""
Export backtest artifacts to web-readable JSON files.

Usage after a backtest run:
    from web.exporter import export_run
    export_run(run_id, run_result=result)
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.reporting.benchmark import build_benchmark_curves_from_net_value_payload
from backtest.reporting.fills_ledger import build_fills_ledger_payload
from backtest.reporting.market_state import compute_market_state_from_net_value_payload, summarize_market_state_payload
from backtest.result import AssessPayload, CurvePayload, RunResult
from core.artifact_schema import (
    ARTIFACT_SCHEMA_VERSION,
    MARKET_STATE_SCHEMA_VERSION,
    NET_VALUE_SCHEMA_VERSION,
    TRADES_SCHEMA_VERSION,
    artifact_schema_fields,
)
from web.export_common import (
    EXECUTION_COLUMNS,
    ORDER_COLUMNS,
    _resolved_trades_df,
    _safe_float,
    _sorted_return_frames,
)
from web.export_execution import _build_execution_payload, _build_order_rows_from_portfolio_orders
from web.export_holdings import build_daily_holdings_payload


def _web_data_dir(run_id: str) -> Path:
    return Path(__file__).resolve().parent / "data" / run_id


def _df_to_json_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert DataFrame to JSON-safe list of dicts."""
    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in df.columns:
            val = row[col]
            missing = pd.isna(val)
            if isinstance(missing, bool) and missing:
                rec[col] = None
            elif isinstance(val, (pd.Timestamp,)):
                rec[col] = val.strftime("%Y-%m-%d")
            elif hasattr(val, "item"):
                scalar = val.item()
                rec[col] = (
                    None
                    if isinstance(scalar, (int, float))
                    and not math.isfinite(float(scalar))
                    else scalar
                )
            elif isinstance(val, (int, float)) and not math.isfinite(float(val)):
                rec[col] = None
            else:
                rec[col] = val
        records.append(rec)
    return records


def build_net_value_payload(result: RunResult) -> dict[str, Any] | None:
    payload = result.payload
    df = payload.equity_curve if isinstance(payload, CurvePayload) else None
    if not isinstance(df, pd.DataFrame) or "trade_date" not in df.columns:
        return None

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = (
        df.dropna(subset=["trade_date"])
        .sort_values("trade_date", kind="mergesort")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
    )
    if df.empty:
        return None
    df["trade_date"] = df["trade_date"].dt.strftime("%Y-%m-%d")
    data: dict[str, Any] = {
        **artifact_schema_fields("net_value", NET_VALUE_SCHEMA_VERSION),
        "dates": df["trade_date"].tolist(),
    }

    # ``net_value`` is the required body of this artifact.  Returning a
    # dates-only payload produces a generation that cannot pass the cache
    # contract and hides the actual malformed result behind a publication
    # failure, so reject it before materializing anything.
    if "sum_net_value" not in df.columns:
        return None

    nv = pd.to_numeric(df["sum_net_value"], errors="coerce")
    if nv.isna().any():
        return None
    nv_values = [float(value) for value in nv]
    if any(not math.isfinite(value) or value <= 0 for value in nv_values):
        return None
    data["net_value"] = [round(value, 6) for value in nv_values]
    peak = nv.cummax().clip(lower=1.0)
    dd = (nv - peak) / peak
    data["drawdown"] = [round(_safe_float(v), 6) for v in dd]

    if "utilization_rate" in df.columns:
        util = pd.to_numeric(df["utilization_rate"], errors="coerce").fillna(0.0)
        data["utilization"] = [round(_safe_float(v), 4) for v in util]

    nv_cols = [
        c for c in df.columns
        if c.endswith("_net_value") and c not in ("sum_net_value", "avg_net_value")
    ]
    if nv_cols:
        data["portfolios"] = {}
        for col in nv_cols:
            name = col.replace("_with_returns_net_value", "")
            vals = pd.to_numeric(df[col], errors="coerce").fillna(1.0)
            data["portfolios"][name] = [round(_safe_float(v), 6) for v in vals]
    return data


def build_market_state_payload(result: RunResult, *, name: str = "", cfg=None) -> dict[str, Any] | None:
    nv_data = build_net_value_payload(result)
    if not isinstance(nv_data, dict):
        return None
    benchmark_payload = (
        build_benchmark_curves_from_net_value_payload(nv_data, cfg=cfg)
        if cfg is not None
        else {}
    )
    if benchmark_payload:
        nv_data = {**nv_data, **benchmark_payload}
    payload = compute_market_state_from_net_value_payload(nv_data, name=name)
    if isinstance(payload, dict):
        payload = {
            **artifact_schema_fields("market_state", MARKET_STATE_SCHEMA_VERSION),
            **payload,
        }
    return payload


def build_trades_payload(result: RunResult) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        **artifact_schema_fields("trades", TRADES_SCHEMA_VERSION),
    }
    result_payload = result.payload
    execution = (
        result_payload.execution
        if isinstance(result_payload, (CurvePayload, AssessPayload))
        else None
    )
    stage_diagnostics = getattr(execution, "diagnostics", None)
    if isinstance(stage_diagnostics, dict) and stage_diagnostics:
        payload["diagnostics"] = stage_diagnostics

    df = _resolved_trades_df(result)
    if isinstance(df, pd.DataFrame) and not df.empty:
        payload["columns"] = list(df.columns)
        payload["records"] = _df_to_json_safe(df)

    execution_rows, execution_meta = _build_execution_payload(result)
    if execution_rows:
        payload["execution_columns"] = EXECUTION_COLUMNS
        payload["execution_records"] = execution_rows
    if execution_meta:
        payload["execution_meta"] = execution_meta

    order_rows, order_meta = _build_order_rows_from_portfolio_orders(result)
    if order_rows:
        payload["order_columns"] = ORDER_COLUMNS
        payload["order_records"] = order_rows
    if order_meta:
        payload["order_meta"] = order_meta

    fills_ledger = build_fills_ledger_payload(result)
    if isinstance(fills_ledger, dict):
        payload["fills_ledger"] = fills_ledger

    return payload if len(payload) > 2 else None


def build_positions_payload(result: RunResult) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for name, df in _sorted_return_frames(result):
        keep_cols = []
        for col in (
            "trade_date", "stock_code", "direction", "open", "close",
            "net_value", "position", "buy_trade", "sell_trade",
            "return_pct", "utilization_rate", "effective_position_ratio",
        ):
            if col in df.columns:
                keep_cols.append(col)
        if not keep_cols:
            continue
        sub = df[keep_cols].copy()
        if "trade_date" in sub.columns:
            sub["trade_date"] = pd.to_datetime(sub["trade_date"]).dt.strftime("%Y-%m-%d")
        positions.append({"name": name, "records": _df_to_json_safe(sub)})
    return positions


def build_positions_cache_payload(result: RunResult) -> dict[str, Any] | None:
    records = build_positions_payload(result)
    if not records:
        return None
    return {
        **artifact_schema_fields("positions", ARTIFACT_SCHEMA_VERSION),
        "records": records,
    }


def build_position_changes_payload(result: RunResult) -> dict[str, Any] | None:
    """Project explicit risk-control events from RunResult for Web review."""
    payload = result.payload
    execution = payload.execution if isinstance(payload, (CurvePayload, AssessPayload)) else None
    events = getattr(execution, "risk_control_events_df", None)
    if not isinstance(events, pd.DataFrame) or events.empty:
        return None

    work = events.copy()
    sort_columns = [
        column
        for column in ("trade_date", "date", "trigger_date", "end_date", "portfolio")
        if column in work.columns
    ]
    for column in sort_columns:
        if "date" in column:
            work[column] = pd.to_datetime(work[column], errors="coerce")
    if sort_columns:
        work = work.sort_values(sort_columns, kind="mergesort", na_position="last")
    work = work.reset_index(drop=True)
    records = _df_to_json_safe(work)
    if not records:
        return None
    return {
        **artifact_schema_fields("position_changes", ARTIFACT_SCHEMA_VERSION),
        "records": records,
    }


def _write_json_if_present(path: Path, payload: Any) -> None:
    if payload is None:
        return
    if isinstance(payload, list) and not payload:
        return
    if isinstance(payload, dict) and not payload:
        return
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def export_run(
    run_id: str,
    *,
    run_result: RunResult,
    cfg=None,
) -> Path:
    """
    Export run artifacts to web/data/{run_id}/ as JSON files.

    Returns the export directory path.
    """
    out_dir = _web_data_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(run_result, RunResult):
        raise TypeError("export_run requires RunResult")

    _export_net_value(run_result, out_dir)
    _export_trades(run_result, out_dir)
    _export_positions(run_result, out_dir)
    _export_daily_holdings(run_result, out_dir)
    _export_market_state(run_result, out_dir, cfg=cfg)

    return out_dir


def _export_net_value(result: RunResult, out_dir: Path) -> None:
    """Export merged net value curve."""
    _write_json_if_present(out_dir / "net_value.json", build_net_value_payload(result))


def _export_trades(result: RunResult, out_dir: Path) -> None:
    """Export trade segment summary."""
    _write_json_if_present(out_dir / "trades.json", build_trades_payload(result))


def _export_positions(result: RunResult, out_dir: Path) -> None:
    """Export per-portfolio position details."""
    _write_json_if_present(out_dir / "positions.json", build_positions_payload(result))


def _export_daily_holdings(result: RunResult, out_dir: Path) -> None:
    """Export per-day holdings snapshots."""
    _write_json_if_present(out_dir / "daily_holdings.json", build_daily_holdings_payload(result))


def _export_market_state(result: RunResult, out_dir: Path, *, cfg=None) -> None:
    """Export market-state slices computed from the net-value curve."""
    payload = build_market_state_payload(result, cfg=cfg)
    _write_json_if_present(out_dir / "market_state.json", payload)
    print(summarize_market_state_payload(payload))


__all__ = [
    "build_daily_holdings_payload",
    "build_market_state_payload",
    "build_net_value_payload",
    "build_position_changes_payload",
    "build_positions_cache_payload",
    "build_positions_payload",
    "build_trades_payload",
    "export_run",
]

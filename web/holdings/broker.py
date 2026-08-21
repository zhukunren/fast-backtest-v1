"""Project ledger snapshots and fills into daily holdings."""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.result import CurvePayload, LedgerExecutionArtifacts, RunResult
from web.export_common import _clean_float, _clean_int, _normalize_holding_code_text, _slot_label
from web.holdings.common import (
    append_daily_holding_row,
    build_daily_summary_row,
    holding_state_key,
    net_value_frame,
    normalize_trade_date_key,
    sum_holding_value,
    sum_row_metric,
)


def _ledger_execution(result: RunResult) -> LedgerExecutionArtifacts | None:
    payload = result.payload
    execution = payload.execution if isinstance(payload, CurvePayload) else None
    return execution if isinstance(execution, LedgerExecutionArtifacts) else None


def _normalized_portfolio_fills(artifacts: RunResult) -> pd.DataFrame:
    execution = _ledger_execution(artifacts)
    if execution is None:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    if not execution.fills_df.empty:
        base_df = execution.fills_df.copy()
        if "source" in base_df.columns and not execution.risk_fills_df.empty:
            source_series = base_df["source"].astype(str).str.strip().str.lower()
            base_df = base_df.loc[~source_series.eq("risk")].copy()
        frames.append(base_df)
    if not execution.risk_fills_df.empty:
        frames.append(execution.risk_fills_df.copy())
    if not frames:
        return pd.DataFrame()

    work = pd.concat(frames, ignore_index=True)
    if "trade_date" not in work.columns:
        return pd.DataFrame()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce").dt.normalize()
    work = work.dropna(subset=["trade_date"])
    if work.empty:
        return pd.DataFrame()
    # Fills from a partially populated broker result are valid input to the
    # exporter, but cannot contribute to holdings without a security code.
    # ``DataFrame.get`` returns a scalar default, so calling ``.map`` on it
    # would raise when the column is absent.
    if "stock_code" not in work.columns:
        return pd.DataFrame()
    work["stock_code"] = work["stock_code"].map(_normalize_holding_code_text)
    work = work.loc[work["stock_code"].ne("")].copy()
    if work.empty:
        return pd.DataFrame()
    if "side" in work.columns:
        work["side"] = work["side"].astype(str).str.strip().str.lower()
    else:
        work["side"] = ""
    if "qty" in work.columns:
        work["qty"] = pd.to_numeric(work["qty"], errors="coerce").fillna(0).astype(int)
    else:
        work["qty"] = 0
    for column in ("cash_delta", "price", "total_cost"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
        else:
            work[column] = float("nan")
    work["_seq"] = pd.Series(range(len(work)), index=work.index, dtype="int64")
    if "order_id" in work.columns:
        order_num = work["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        work["_order_num"] = pd.to_numeric(order_num, errors="coerce")
    else:
        work["_order_num"] = pd.Series(float("nan"), index=work.index, dtype="float64")
    return work.sort_values(["trade_date", "_order_num", "_seq"], kind="mergesort").reset_index(drop=True)


def build_fill_accounting_snapshots(
    artifacts: RunResult,
    *,
    ordered_dates: list[str],
) -> tuple[dict[str, dict[tuple[str, str], dict[str, Any]]], dict[str, float]]:
    work = _normalized_portfolio_fills(artifacts)
    if work.empty or not ordered_dates:
        return {}, {}

    fills_by_date: dict[str, list[dict[str, Any]]] = {}
    for _, row in work.iterrows():
        date_key = normalize_trade_date_key(row.get("trade_date"))
        if not date_key:
            continue
        slot_raw = str(row.get("slot_id", "") or "").strip()
        slot_label = _slot_label(slot_raw) if slot_raw else "组合"
        fills_by_date.setdefault(date_key, []).append(
            {
                "slot": slot_label,
                "stock_code": str(row.get("stock_code", "") or "").strip(),
                "side": str(row.get("side", "") or "").strip().lower(),
                "qty": int(row.get("qty", 0) or 0),
                "cash_delta": _clean_float(row.get("cash_delta")),
                "price": _clean_float(row.get("price")),
                "total_cost": _clean_float(row.get("total_cost")),
            }
        )

    states_by_date: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    realized_cum_by_date: dict[str, float] = {}
    current_positions: dict[tuple[str, str], dict[str, Any]] = {}
    realized_cum = 0.0

    for date_key in ordered_dates:
        for fill in fills_by_date.get(date_key, []):
            key = holding_state_key(fill["slot"], fill["stock_code"])
            side = str(fill.get("side", "") or "").strip().lower()
            qty = int(fill.get("qty", 0) or 0)
            if qty <= 0 or not key[1]:
                continue
            cash_delta = _clean_float(fill.get("cash_delta"))
            price = _clean_float(fill.get("price"))
            total_cost = _clean_float(fill.get("total_cost")) or 0.0
            state = current_positions.get(key, {"qty": 0, "cost_basis": 0.0})
            current_qty = int(state.get("qty", 0) or 0)
            current_basis = float(state.get("cost_basis", 0.0) or 0.0)

            if side == "buy":
                basis_increase = (
                    -float(cash_delta)
                    if cash_delta is not None and cash_delta < 0
                    else float(qty) * float(price or 0.0) + float(total_cost)
                )
                current_positions[key] = {
                    "qty": current_qty + qty,
                    "cost_basis": current_basis + float(basis_increase),
                }
                continue

            if side != "sell" or current_qty <= 0:
                continue
            sell_qty = min(current_qty, qty)
            if sell_qty <= 0:
                continue
            scale = float(sell_qty) / float(qty) if qty > 0 else 0.0
            avg_cost = current_basis / float(current_qty) if current_qty > 0 else 0.0
            allocated_cost = avg_cost * float(sell_qty)
            proceeds = (
                float(cash_delta) * scale
                if cash_delta is not None
                else float(sell_qty) * float(price or 0.0) - float(total_cost) * scale
            )
            realized_cum += proceeds - allocated_cost
            remain_qty = current_qty - sell_qty
            remain_basis = max(current_basis - allocated_cost, 0.0)
            if remain_qty > 0:
                current_positions[key] = {"qty": remain_qty, "cost_basis": remain_basis}
            else:
                current_positions.pop(key, None)

        snapshot: dict[tuple[str, str], dict[str, Any]] = {}
        for key, state in current_positions.items():
            qty = int(state.get("qty", 0) or 0)
            cost_basis = float(state.get("cost_basis", 0.0) or 0.0)
            if qty <= 0:
                continue
            snapshot[key] = {
                "qty": qty,
                "cost_basis": cost_basis,
                "avg_cost": cost_basis / float(qty) if qty > 0 else None,
            }
        states_by_date[date_key] = snapshot
        realized_cum_by_date[date_key] = round(realized_cum, 2)

    return states_by_date, realized_cum_by_date


def build_daily_summary(
    artifacts: RunResult,
    *,
    holdings_by_date: dict[str, list[dict[str, Any]]],
    realized_cum_by_date: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    merged_df = net_value_frame(artifacts)
    if not isinstance(merged_df, pd.DataFrame) or merged_df.empty or "trade_date" not in merged_df.columns:
        return {}

    work = merged_df.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work = work.dropna(subset=["trade_date"]).sort_values("trade_date", kind="mergesort")
    if work.empty:
        return {}

    summary_by_date: dict[str, dict[str, Any]] = {}
    initial_asset: float | None = None
    prev_asset: float | None = None
    for _, row in work.iterrows():
        date_key = normalize_trade_date_key(row.get("trade_date"))
        if not date_key:
            continue
        rows = holdings_by_date.get(date_key, [])
        unrealized_pnl = sum_row_metric(rows, "unrealized_pnl")
        asset = _clean_float(row.get("equity"))
        cash = _clean_float(row.get("cash"))
        position_value = _clean_float(row.get("position_value"))
        if position_value is None:
            position_value = sum_holding_value(rows)
        if position_value is None and not rows:
            position_value = 0.0
        if cash is None and asset is not None and position_value is not None:
            cash = round(float(asset) - float(position_value), 2)
        if cash is None and asset is not None and not rows:
            cash = round(float(asset), 2)

        if asset is not None and initial_asset is None:
            initial_asset = float(asset)
        if asset is not None:
            daily_pnl = 0.0 if prev_asset is None else float(asset) - float(prev_asset)
            cum_pnl = float(asset) - float(initial_asset) if initial_asset is not None else None
            prev_asset = float(asset)
        else:
            daily_pnl = None
            cum_pnl = None

        realized_pnl = (
            _clean_float(realized_cum_by_date.get(date_key))
            if isinstance(realized_cum_by_date, dict) and date_key in realized_cum_by_date
            else (
                round(float(cum_pnl) - float(unrealized_pnl), 2)
                if cum_pnl is not None and unrealized_pnl is not None
                else None
            )
        )
        daily_return = row.get("daily_return")
        if daily_return is None and asset is not None and prev_asset is not None:
            prev_for_return = float(asset) - float(daily_pnl or 0.0)
            if abs(prev_for_return) > 1e-12:
                daily_return = float(asset) / prev_for_return - 1.0

        summary_by_date[date_key] = build_daily_summary_row(
            net_value=row.get("sum_net_value", row.get("net_value")),
            asset=asset,
            cash=cash,
            position_value=position_value,
            cum_pnl=cum_pnl,
            daily_pnl=daily_pnl,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            daily_return=daily_return,
            holding_count=len(rows),
        )
    return summary_by_date


def build_daily_holdings(artifacts: RunResult) -> dict[str, list[dict[str, Any]]] | None:
    merged_df = net_value_frame(artifacts)
    if not isinstance(merged_df, pd.DataFrame) or merged_df.empty:
        return None
    execution = _ledger_execution(artifacts)
    snapshots = execution.daily_position_snapshots if execution is not None else ()
    if not snapshots:
        return None

    work = merged_df.copy()
    if "trade_date" not in work.columns:
        return None
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce").dt.normalize()
    work = work.dropna(subset=["trade_date"])
    if work.empty:
        return None

    metrics_by_date: dict[pd.Timestamp, dict[str, Any]] = {}
    for _, row in work.iterrows():
        trade_date = pd.Timestamp(row["trade_date"]).normalize()
        metrics_by_date[trade_date] = {
            "net_value": row.get("sum_net_value", row.get("net_value")),
            "asset": row.get("equity"),
            "raw_asset": row.get("equity_raw", row.get("equity")),
            "portfolio_raw_ratio": (
                row.get("utilization_rate_raw")
                if "utilization_rate_raw" in row.index
                else row.get("utilization_rate") if "utilization_rate" in row.index else None
            ),
            "portfolio_effective_ratio": (
                row.get("effective_position_ratio")
                if "effective_position_ratio" in row.index
                else row.get("utilization_rate") if "utilization_rate" in row.index else None
            ),
        }

    by_date: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        trade_date = pd.to_datetime(snapshot.get("trade_date"), errors="coerce")
        if pd.isna(trade_date):
            continue
        trade_date = pd.Timestamp(trade_date).normalize()
        metrics = metrics_by_date.get(trade_date, {})
        holdings: list[dict[str, Any]] = []
        positions = [item for item in (snapshot.get("positions", []) or []) if isinstance(item, dict)]
        single_position_snapshot = len(positions) == 1
        for item in positions:
            shares = _clean_int(item.get("qty"))
            close = _clean_float(item.get("close"))
            raw_holding_value = _clean_float(item.get("holding_value_raw"))
            if raw_holding_value is None and shares is not None and close is not None:
                raw_holding_value = round(float(shares) * float(close), 2)
            raw_asset = _clean_float(metrics.get("raw_asset"))
            asset = _clean_float(metrics.get("asset"))
            row_util = _clean_float(item.get("utilization_rate"))
            row_effective_ratio = _clean_float(item.get("effective_position_ratio"))
            portfolio_raw_ratio = _clean_float(metrics.get("portfolio_raw_ratio"))
            portfolio_effective_ratio = _clean_float(metrics.get("portfolio_effective_ratio"))
            has_ratio_context = any(
                value is not None
                for value in (row_util, row_effective_ratio, portfolio_raw_ratio, portfolio_effective_ratio)
            )
            if (
                has_ratio_context
                and row_util is None
                and raw_holding_value is not None
                and raw_asset is not None
                and raw_asset > 1e-12
            ):
                row_util = float(raw_holding_value) / float(raw_asset)
            if row_util is None and single_position_snapshot:
                row_util = portfolio_raw_ratio
            if row_effective_ratio is None and single_position_snapshot:
                row_effective_ratio = portfolio_effective_ratio
            scale = 1.0
            if (
                row_util is not None
                and row_effective_ratio is not None
                and row_util > 1e-12
                and abs(row_util - row_effective_ratio) > 1e-9
            ):
                scale = max(min(float(row_effective_ratio) / float(row_util), 1.0), 0.0)
            elif (
                portfolio_raw_ratio is not None
                and portfolio_effective_ratio is not None
                and portfolio_raw_ratio > 1e-12
                and abs(float(portfolio_raw_ratio) - float(portfolio_effective_ratio)) > 1e-9
            ):
                scale = max(min(float(portfolio_effective_ratio) / float(portfolio_raw_ratio), 1.0), 0.0)
            holding_value = _clean_float(item.get("holding_value"))
            if holding_value is None and raw_holding_value is not None:
                holding_value = round(float(raw_holding_value) * float(scale), 2)
            if holding_value is None and close is None:
                continue
            if (
                has_ratio_context
                and row_effective_ratio is None
                and holding_value is not None
                and asset is not None
                and asset > 1e-12
            ):
                row_effective_ratio = float(holding_value) / float(asset)
            if has_ratio_context and row_util is None and row_effective_ratio is not None:
                row_util = row_effective_ratio
            slot = item.get("slot")
            if not str(slot or "").strip():
                slot_id = str(item.get("slot_id", "") or "").strip()
                slot = _slot_label(slot_id) if slot_id else "组合"
            shares_scaled = abs(scale - 1.0) > 1e-9
            append_daily_holding_row(
                rows=holdings,
                slot=str(slot or "组合"),
                code=item.get("stock_code"),
                name=item.get("stock_name"),
                direction=item.get("direction"),
                close=close,
                shares=shares if not shares_scaled else None,
                original_shares=shares,
                shares_scaled=shares_scaled,
                holding_value=holding_value,
                net_value=item.get("net_value", metrics.get("net_value")),
                utilization_rate=row_util,
                effective_position_ratio=row_effective_ratio,
            )
        if holdings:
            by_date[trade_date.strftime("%Y-%m-%d")] = holdings
    return by_date or None


__all__ = [
    "build_daily_holdings",
    "build_daily_summary",
    "build_fill_accounting_snapshots",
]

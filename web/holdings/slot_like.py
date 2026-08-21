"""Project slot and legacy processed frames into daily holdings."""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.result import RunResult
from web.export_common import (
    _clean_float,
    _clean_int,
    _normalize_holding_code_text,
    _normalize_initial_asset_hint,
    _resolve_slot_initial_asset_hint,
    _slot_label,
    _sorted_return_frames,
)
from web.holdings.common import (
    append_daily_holding_row,
    average_holding_net_value,
    build_daily_summary_row,
    holding_state_key,
    net_value_frame,
    normalize_holding_value,
    normalize_trade_date_key,
    sum_holding_value,
    sum_row_metric,
)


def build_cost_snapshots(
    artifacts: RunResult,
) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    out: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for slot_name, df in _sorted_return_frames(artifacts):
        if (
            not isinstance(df, pd.DataFrame)
            or df.empty
            or "trade_date" not in df.columns
            or "stock_code" not in df.columns
        ):
            continue
        slot_label = _slot_label(slot_name)
        work = df.copy()
        work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
        work = (
            work.dropna(subset=["trade_date"])
            .sort_values("trade_date", kind="mergesort")
            .reset_index(drop=True)
        )
        if work.empty:
            continue
        current_code = ""
        current_cost: float | None = None
        for _, row in work.iterrows():
            code = _normalize_holding_code_text(row.get("stock_code"))
            date_key = normalize_trade_date_key(row.get("trade_date"))
            if not date_key or not code:
                current_code = ""
                current_cost = None
                continue
            buy_price = _clean_float(row.get("buy_price"))
            if code != current_code:
                current_code = code
                current_cost = buy_price
            elif buy_price is not None:
                current_cost = buy_price
            raw_shares = _clean_int(row.get("shares"))
            out.setdefault(date_key, {})[holding_state_key(slot_label, code)] = {
                "qty": raw_shares,
                "avg_cost": current_cost,
            }
    return out


def _terminal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the final end-of-day row for each slot/date snapshot."""
    if not isinstance(df, pd.DataFrame) or df.empty or "trade_date" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work = work.dropna(subset=["trade_date"]).reset_index(drop=True)
    if work.empty:
        return pd.DataFrame()
    work["_row_seq"] = pd.Series(range(len(work)), index=work.index, dtype="int64")
    work = work.sort_values(["trade_date", "_row_seq"], kind="mergesort")
    terminal = work.groupby("trade_date", sort=False, group_keys=False).tail(1).copy()
    return terminal.sort_values(["trade_date", "_row_seq"], kind="mergesort").drop(
        columns=["_row_seq"]
    )


def _collect_account_metrics_by_date(
    artifacts: RunResult,
) -> dict[str, dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for _, df in _sorted_return_frames(artifacts):
        if not isinstance(df, pd.DataFrame) or df.empty or "trade_date" not in df.columns:
            continue
        work = _terminal_rows(df)
        if work.empty:
            continue
        for _, row in work.iterrows():
            date_key = normalize_trade_date_key(row.get("trade_date"))
            if not date_key:
                continue
            entry = by_date.setdefault(
                date_key,
                {
                    "asset": 0.0,
                    "cash": 0.0,
                    "position_value": 0.0,
                    "has_asset": False,
                    "has_cash": False,
                    "has_position_value": False,
                },
            )
            asset = _clean_float(row.get("equity"))
            effective_ratio = row.get(
                "effective_position_ratio",
                row.get("utilization_rate"),
            )
            position_value = normalize_holding_value(
                position_value_abs=row.get("position_value_abs"),
                equity=row.get("equity"),
                effective_ratio=effective_ratio,
                shares=row.get("shares"),
                close=row.get("close"),
            )
            cash = _clean_float(row.get("cash"))
            if asset is not None:
                entry["asset"] += float(asset)
                entry["has_asset"] = True
            if position_value is not None:
                entry["position_value"] += float(position_value)
                entry["has_position_value"] = True
            if cash is not None:
                entry["cash"] += float(cash)
                entry["has_cash"] = True
    return by_date


def _resolve_initial_asset(
    *,
    ordered_dates: list[str],
    merged_metrics: dict[str, dict[str, Any]],
    account_metrics: dict[str, dict[str, Any]],
    initial_asset_hint: Any = None,
) -> float | None:
    hinted_asset = _normalize_initial_asset_hint(initial_asset_hint)
    if hinted_asset is not None:
        return hinted_asset
    for date_key in ordered_dates:
        slot_metrics = account_metrics.get(date_key, {})
        if not slot_metrics.get("has_asset"):
            continue
        asset = _clean_float(slot_metrics.get("asset"))
        net_value = _clean_float(merged_metrics.get(date_key, {}).get("net_value"))
        if asset is None:
            continue
        if net_value is not None and abs(float(net_value) - 1.0) <= 1e-6:
            return float(asset)
    for date_key in ordered_dates:
        slot_metrics = account_metrics.get(date_key, {})
        if not slot_metrics.get("has_asset"):
            continue
        asset = _clean_float(slot_metrics.get("asset"))
        if asset is not None:
            return float(asset)
    return None


def build_daily_summary(
    artifacts: RunResult,
    *,
    holdings_by_date: dict[str, list[dict[str, Any]]],
    realized_cum_by_date: dict[str, float] | None = None,
    initial_asset_hint: Any = None,
) -> dict[str, dict[str, Any]]:
    merged_df = net_value_frame(artifacts)
    merged_metrics: dict[str, dict[str, Any]] = {}
    ordered_dates: list[str] = []
    if (
        isinstance(merged_df, pd.DataFrame)
        and not merged_df.empty
        and "trade_date" in merged_df.columns
    ):
        work = merged_df.copy()
        work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
        work = work.dropna(subset=["trade_date"]).sort_values(
            "trade_date",
            kind="mergesort",
        )
        for _, row in work.iterrows():
            date_key = normalize_trade_date_key(row.get("trade_date"))
            if not date_key:
                continue
            ordered_dates.append(date_key)
            merged_metrics[date_key] = {
                "net_value": row.get("sum_net_value", row.get("net_value")),
                "daily_return": row.get("daily_return"),
            }

    account_metrics = _collect_account_metrics_by_date(artifacts)
    all_dates = ordered_dates or sorted(set(account_metrics) | set(holdings_by_date))
    initial_asset = _resolve_initial_asset(
        ordered_dates=all_dates,
        merged_metrics=merged_metrics,
        account_metrics=account_metrics,
        initial_asset_hint=initial_asset_hint,
    )

    summary_by_date: dict[str, dict[str, Any]] = {}
    prev_asset: float | None = None
    for date_key in all_dates:
        metrics = merged_metrics.get(date_key, {})
        slot_metrics = account_metrics.get(date_key, {})
        rows = holdings_by_date.get(date_key, [])
        unrealized_pnl = sum_row_metric(rows, "unrealized_pnl")
        net_value = _clean_float(metrics.get("net_value"))
        position_value = (
            round(float(slot_metrics["position_value"]), 2)
            if slot_metrics.get("has_position_value")
            else sum_holding_value(rows)
        )
        asset = (
            round(float(initial_asset) * float(net_value), 2)
            if initial_asset is not None and net_value is not None
            else None
        )
        if asset is None and slot_metrics.get("has_asset"):
            asset = round(float(slot_metrics["asset"]), 2)
        cash = (
            round(float(asset) - float(position_value), 2)
            if asset is not None and position_value is not None
            else None
        )
        if cash is None and slot_metrics.get("has_cash"):
            cash = round(float(slot_metrics["cash"]), 2)
        if cash is None and asset is not None and position_value is not None:
            cash = round(float(asset) - float(position_value), 2)
        if position_value is None and asset is not None and cash is not None:
            position_value = round(float(asset) - float(cash), 2)
        if position_value is None and not rows:
            position_value = 0.0
        if cash is None and asset is not None and not rows:
            cash = round(float(asset), 2)

        if asset is not None:
            daily_pnl = 0.0 if prev_asset is None else float(asset) - float(prev_asset)
            cum_pnl = (
                float(asset) - float(initial_asset)
                if initial_asset is not None
                else None
            )
            prev_asset = float(asset)
        else:
            daily_pnl = None
            cum_pnl = None

        realized_pnl = (
            _clean_float(realized_cum_by_date.get(date_key))
            if isinstance(realized_cum_by_date, dict)
            and date_key in realized_cum_by_date
            else (
                round(float(cum_pnl) - float(unrealized_pnl), 2)
                if cum_pnl is not None and unrealized_pnl is not None
                else None
            )
        )

        daily_return = metrics.get("daily_return")
        if daily_return is None and asset is not None and prev_asset is not None:
            prev_for_return = float(asset) - float(daily_pnl or 0.0)
            if abs(prev_for_return) > 1e-12:
                daily_return = float(asset) / prev_for_return - 1.0

        summary_by_date[date_key] = build_daily_summary_row(
            net_value=(
                net_value
                if net_value is not None
                else average_holding_net_value(rows)
            ),
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


def build_daily_holdings(
    artifacts: RunResult,
    *,
    initial_asset_hint: Any = None,
) -> dict[str, list[dict[str, Any]]] | None:
    optional_columns = (
        "stock_name",
        "direction",
        "close",
        "shares",
        "position_value_abs",
        "equity",
        "net_value",
        "position",
        "utilization_rate",
        "utilization_rate_raw",
        "effective_position_ratio",
    )
    frames: list[pd.DataFrame] = []
    for slot_name, df in _sorted_return_frames(artifacts):
        slot_label = _slot_label(slot_name)
        if "trade_date" not in df.columns or "stock_code" not in df.columns:
            continue
        keep_cols = [
            col
            for col in (
                "trade_date",
                "stock_code",
                "stock_name",
                "direction",
                "close",
                "shares",
                "position_value_abs",
                "equity",
                "net_value",
                "position",
                "utilization_rate",
                "utilization_rate_raw",
                "effective_position_ratio",
            )
            if col in df.columns
        ]
        sub = df[keep_cols].copy()
        # Processed frames may carry attrs containing DataFrames; pandas concat
        # must not compare those embedded objects.
        sub.attrs = {}
        sub = sub.dropna(axis="columns", how="all")
        sub["slot"] = slot_label
        frames.append(sub)

    if not frames:
        return None

    all_df = pd.concat(frames, ignore_index=True)
    for col in optional_columns:
        if col not in all_df.columns:
            all_df[col] = None
    all_df["stock_code"] = all_df["stock_code"].map(_normalize_holding_code_text)
    all_df = all_df.loc[all_df["stock_code"].ne("")]
    if all_df.empty:
        return None

    all_df["trade_date"] = pd.to_datetime(all_df["trade_date"], errors="coerce")
    all_df = all_df.dropna(subset=["trade_date"])
    if all_df.empty:
        return None

    raw_ratio = pd.to_numeric(all_df["utilization_rate_raw"], errors="coerce")
    effective_ratio_source = all_df["effective_position_ratio"].where(
        all_df["effective_position_ratio"].notna(),
        all_df["utilization_rate"],
    )
    if "position" in all_df.columns:
        effective_ratio_source = effective_ratio_source.where(
            effective_ratio_source.notna(),
            all_df["position"],
        )
    effective_ratio = pd.to_numeric(effective_ratio_source, errors="coerce")
    share_series = pd.to_numeric(all_df["shares"], errors="coerce")
    scaled_mask = (
        raw_ratio.notna()
        & effective_ratio.notna()
        & ((raw_ratio - effective_ratio).abs() > 1e-9)
    )
    slot_initial_asset = _resolve_slot_initial_asset_hint(
        initial_asset_hint,
        len(frames),
    )
    resolved_equity = pd.to_numeric(all_df["equity"], errors="coerce")
    if slot_initial_asset is not None:
        net_value_series = pd.to_numeric(all_df["net_value"], errors="coerce")
        resolved_equity = resolved_equity.where(
            resolved_equity.notna(),
            net_value_series * float(slot_initial_asset),
        )
    all_df["original_shares"] = share_series
    all_df["shares_scaled"] = scaled_mask
    all_df["shares"] = share_series.where(~scaled_mask)
    all_df["effective_position_ratio"] = effective_ratio
    all_df["resolved_equity"] = resolved_equity
    all_df["holding_value"] = all_df.apply(
        lambda row: normalize_holding_value(
            position_value_abs=row.get("position_value_abs"),
            equity=row.get("resolved_equity"),
            effective_ratio=row.get("effective_position_ratio"),
            shares=row.get("shares"),
            close=row.get("close"),
        ),
        axis=1,
    )

    all_df = all_df.sort_values(["trade_date", "slot"], kind="mergesort").reset_index(
        drop=True
    )
    all_df["trade_date"] = all_df["trade_date"].dt.strftime("%Y-%m-%d")

    by_date: dict[str, list[dict[str, Any]]] = {}
    for date_str, group in all_df.groupby("trade_date", sort=True):
        holdings: list[dict[str, Any]] = []
        for _, row in group.iterrows():
            append_daily_holding_row(
                rows=holdings,
                slot=row.get("slot"),
                code=row.get("stock_code"),
                name=row.get("stock_name"),
                direction=row.get("direction"),
                close=row.get("close"),
                shares=row.get("shares"),
                original_shares=row.get("original_shares"),
                shares_scaled=row.get("shares_scaled"),
                holding_value=row.get("holding_value"),
                net_value=row.get("net_value"),
                utilization_rate=row.get("utilization_rate"),
                effective_position_ratio=row.get("effective_position_ratio"),
            )
        if holdings:
            by_date[str(date_str)] = holdings
    return by_date or None


__all__ = [
    "build_cost_snapshots",
    "build_daily_holdings",
    "build_daily_summary",
]

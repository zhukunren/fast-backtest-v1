from __future__ import annotations

import pandas as pd

from core.invariants import ensure_trade_date_normalized


_FILL_COLUMNS = [
    "order_id",
    "slot_id",
    "trade_date",
    "stock_code",
    "stock_name",
    "side",
    "qty",
    "price",
    "commission",
    "stamp_tax",
    "slippage_cost",
    "total_cost",
    "cash_delta",
    "cash_before",
    "cash_after",
    "shares_before",
    "shares_after",
    "exec_price_type",
    "source",
    "note",
]

_ORDER_COLUMNS = [
    "order_id",
    "slot_id",
    "trade_date",
    "stock_code",
    "stock_name",
    "side",
    "qty",
    "filled_qty",
    "price",
    "status",
    "reason",
    "cash_before",
    "cash_after",
    "shares_before",
    "shares_after",
    "exec_price_type",
    "source",
    "note",
]


def _empty_fill_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_FILL_COLUMNS)


def _empty_order_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_ORDER_COLUMNS)


def _normalize_slot_fill_frame(slot_id: str, df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["slot_id"] = str(slot_id)
    out = ensure_trade_date_normalized(out, col="trade_date")
    if "source" not in out.columns:
        out["source"] = "strategy"
    if "_slot_event_seq" not in out.columns:
        out["_slot_event_seq"] = pd.Series(range(1, len(out) + 1), index=out.index, dtype="int64")
    return out


def _normalize_slot_order_frame(slot_id: str, df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["slot_id"] = str(slot_id)
    out = ensure_trade_date_normalized(out, col="trade_date")
    if "source" not in out.columns:
        out["source"] = "strategy"
    if "_slot_event_seq" not in out.columns:
        out["_slot_event_seq"] = pd.Series(range(1, len(out) + 1), index=out.index, dtype="int64")
    if "filled_qty" not in out.columns:
        out["filled_qty"] = pd.to_numeric(out.get("qty"), errors="coerce").fillna(0).astype(int)
    return out


def build_slot_broker_risk_orders(risk_fills_df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(risk_fills_df, pd.DataFrame) or risk_fills_df.empty:
        return pd.DataFrame(columns=_ORDER_COLUMNS)
    work = risk_fills_df.copy()
    if "trade_date" in work.columns:
        work = ensure_trade_date_normalized(work, col="trade_date")
    if "source" not in work.columns:
        work["source"] = "risk"
    work["filled_qty"] = pd.to_numeric(work.get("qty"), errors="coerce").fillna(0).astype(int)
    work["status"] = "filled"
    if "reason" not in work.columns:
        if "risk_reason" in work.columns:
            work["reason"] = work["risk_reason"].astype(str)
        else:
            work["reason"] = ""
    return work.loc[:, [col for col in _ORDER_COLUMNS if col in work.columns]]


def build_slot_broker_orders_and_fills(
    *,
    slot_orders: dict[str, pd.DataFrame] | None,
    slot_fills: dict[str, pd.DataFrame] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    order_map = slot_orders if isinstance(slot_orders, dict) else {}
    fill_map = slot_fills if isinstance(slot_fills, dict) else {}
    slot_ids = sorted(set(order_map) | set(fill_map), key=str.casefold)
    if not slot_ids:
        return _empty_order_df(), _empty_fill_df()

    fill_frames: list[pd.DataFrame] = []
    order_frames: list[pd.DataFrame] = []
    for slot_id in slot_ids:
        slot_fills_df = fill_map.get(slot_id)
        normalized = _normalize_slot_fill_frame(str(slot_id), slot_fills_df)
        if not normalized.empty:
            fill_frames.append(normalized)
        slot_orders_df = order_map.get(slot_id)
        normalized_orders = _normalize_slot_order_frame(str(slot_id), slot_orders_df)
        if not normalized_orders.empty:
            order_frames.append(normalized_orders)

    if fill_frames:
        fills_df = pd.concat(fill_frames, ignore_index=True)
        fills_df = fills_df.sort_values(["trade_date", "slot_id", "_slot_event_seq"], kind="mergesort").reset_index(drop=True)
        fills_df["order_id"] = [f"order_{idx}" for idx in range(1, len(fills_df) + 1)]
        fills_df = fills_df.loc[:, [col for col in _FILL_COLUMNS if col in fills_df.columns]]
    else:
        fills_df = _empty_fill_df()

    if order_frames:
        orders_df = pd.concat(order_frames, ignore_index=True)
        orders_df = orders_df.sort_values(["trade_date", "slot_id", "_slot_event_seq"], kind="mergesort").reset_index(drop=True)
        orders_df["order_id"] = [f"order_{idx}" for idx in range(1, len(orders_df) + 1)]
        if not fills_df.empty and "status" in orders_df.columns:
            filled_order_ids = orders_df.loc[
                orders_df["status"].astype(str).str.strip().str.lower().eq("filled"),
                "order_id",
            ].tolist()
            if len(filled_order_ids) == len(fills_df):
                fills_df["order_id"] = filled_order_ids
        orders_df = orders_df.loc[:, [col for col in _ORDER_COLUMNS if col in orders_df.columns]]
        return orders_df, fills_df

    if fill_frames:
        orders_df = fills_df.loc[
            :,
            [
                "order_id",
                "slot_id",
                "trade_date",
                "stock_code",
                "stock_name",
                "side",
                "qty",
                "price",
                "exec_price_type",
                "source",
                "note",
            ],
        ].copy()
        orders_df["filled_qty"] = orders_df["qty"]
        orders_df["status"] = "filled"
        orders_df["reason"] = ""
        orders_df["cash_before"] = pd.Series(dtype="float64")
        orders_df["cash_after"] = pd.Series(dtype="float64")
        orders_df["shares_before"] = pd.Series(dtype="float64")
        orders_df["shares_after"] = pd.Series(dtype="float64")
        orders_df = orders_df.loc[:, _ORDER_COLUMNS]
        return orders_df, fills_df

    return _empty_order_df(), fills_df


def _build_slot_snapshot_by_date(df: pd.DataFrame) -> dict[pd.Timestamp, dict[str, dict[str, float | int]]]:
    from .slot_broker_risk_fills import _build_slot_snapshot_by_date as _impl

    return _impl(df)


def build_slot_broker_risk_fills(
    *,
    raw_processed_dfs: dict[str, pd.DataFrame] | None,
    risk_processed_dfs: dict[str, pd.DataFrame] | None,
    strategy_fills_dfs: dict[str, pd.DataFrame] | None,
    initial_slot_capital: float,
    lot_size: int,
    allow_partial_fill: bool,
    cost_cfg,
    starting_order_index: int = 1,
    trade_constraints=None,
    quote_resolver=None,
) -> pd.DataFrame:
    from .slot_broker_risk_fills import build_slot_broker_risk_fills as _impl

    return _impl(
        raw_processed_dfs=raw_processed_dfs,
        risk_processed_dfs=risk_processed_dfs,
        strategy_fills_dfs=strategy_fills_dfs,
        initial_slot_capital=initial_slot_capital,
        lot_size=lot_size,
        allow_partial_fill=allow_partial_fill,
        cost_cfg=cost_cfg,
        starting_order_index=starting_order_index,
        trade_constraints=trade_constraints,
        quote_resolver=quote_resolver,
    )


__all__ = ["build_slot_broker_orders_and_fills", "build_slot_broker_risk_fills", "build_slot_broker_risk_orders"]

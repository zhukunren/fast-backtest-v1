"""Normalize rejection ledgers emitted by V1 execution engines."""
from __future__ import annotations

import pandas as pd


_DEDUPE_COLUMNS = (
    "order_id",
    "slot_id",
    "trade_date",
    "stock_code",
    "side",
    "qty",
    "requested_qty",
    "filled_qty",
    "status",
    "reason",
    "constraint_reason",
    "source",
)


def _nonfilled_orders(orders_df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(orders_df, pd.DataFrame) or orders_df.empty or "status" not in orders_df.columns:
        return pd.DataFrame()
    status = orders_df["status"].astype(str).str.strip().str.casefold()
    return orders_df.loc[status.ne("filled")].copy()


def merge_ledger_rejections(
    orders_df: pd.DataFrame | None,
    shadow_rejections_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """Combine source-order and shadow-ledger rejections deterministically."""
    ordinary = _nonfilled_orders(orders_df)
    shadow = (
        shadow_rejections_df.copy()
        if isinstance(shadow_rejections_df, pd.DataFrame) and not shadow_rejections_df.empty
        else pd.DataFrame()
    )
    if not shadow.empty and "status" in shadow.columns:
        status = shadow["status"].astype(str).str.strip().str.casefold()
        shadow = shadow.loc[status.eq("rejected")].copy()
    frames = [frame for frame in (ordinary, shadow) if not frame.empty]
    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True, sort=False)
    dedupe_columns = [column for column in _DEDUPE_COLUMNS if column in merged.columns]
    if dedupe_columns:
        merged = merged.drop_duplicates(subset=dedupe_columns, keep="first")
    else:
        merged = merged.drop_duplicates(keep="first")

    merged["_rejection_seq"] = pd.Series(range(len(merged)), index=merged.index, dtype="int64")
    sort_columns = ["_rejection_seq"]
    if "trade_date" in merged.columns:
        merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.normalize()
        sort_columns.insert(0, "trade_date")
    if "order_id" in merged.columns:
        order_number = merged["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        merged["_rejection_order_number"] = pd.to_numeric(order_number, errors="coerce")
        sort_columns.insert(-1, "_rejection_order_number")
    merged = merged.sort_values(sort_columns, kind="mergesort", na_position="last")
    return merged.drop(
        columns=["_rejection_seq", "_rejection_order_number"],
        errors="ignore",
    ).reset_index(drop=True)


__all__ = ["merge_ledger_rejections"]

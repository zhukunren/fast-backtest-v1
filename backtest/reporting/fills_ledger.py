"""Unified fill-ledger projection across broker and vectorized engines."""
from __future__ import annotations

from typing import Any

import pandas as pd

from core.artifact_schema import FILLS_LEDGER_SCHEMA_VERSION, artifact_schema_fields

from backtest.result import (
    AssessPayload,
    CurvePayload,
    LedgerExecutionArtifacts,
    RunResult,
    VectorizedExecutionArtifacts,
)


FILLS_LEDGER_COLUMNS = [
    "trade_date",
    "portfolio",
    "stock_code",
    "side",
    "qty",
    "price",
    "cash_delta",
    "commission",
    "stamp_tax",
    "slippage_cost",
    "total_cost",
    "source",
    "order_id",
    "engine",
    "note",
]


def _slot_label(name: str) -> str:
    return str(name).replace("df_", "").replace("_with_returns", "").replace("_", "-")


def _format_trade_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value or "")
    return pd.Timestamp(parsed).strftime("%Y-%m-%d")


def _clean_float(value: Any, *, digits: int | None = None) -> float | None:
    try:
        num = float(value)
    except Exception:
        return None
    if pd.isna(num):
        return None
    return round(num, digits) if isinstance(digits, int) else num


def _clean_int(value: Any) -> int | None:
    try:
        num = float(value)
    except Exception:
        return None
    if pd.isna(num):
        return None
    return int(round(num))


def _empty_row() -> dict[str, Any]:
    return {column: None for column in FILLS_LEDGER_COLUMNS}


def _append_row(
    rows: list[dict[str, Any]],
    *,
    trade_date: Any,
    portfolio: str,
    stock_code: str,
    side: str,
    qty: Any,
    price: Any,
    cash_delta: Any = None,
    commission: Any = None,
    stamp_tax: Any = None,
    slippage_cost: Any = None,
    total_cost: Any = None,
    source: str = "strategy",
    order_id: str = "",
    engine: str,
    note: str = "",
) -> None:
    row = _empty_row()
    row["trade_date"] = _format_trade_date(trade_date)
    row["portfolio"] = str(portfolio or "")
    row["stock_code"] = str(stock_code or "")
    row["side"] = str(side or "").strip().lower()
    row["qty"] = _clean_int(qty)
    row["price"] = _clean_float(price, digits=6)
    row["cash_delta"] = _clean_float(cash_delta, digits=6)
    row["commission"] = _clean_float(commission, digits=6)
    row["stamp_tax"] = _clean_float(stamp_tax, digits=6)
    row["slippage_cost"] = _clean_float(slippage_cost, digits=6)
    row["total_cost"] = _clean_float(total_cost, digits=6)
    row["source"] = str(source or "strategy").strip().lower()
    row["order_id"] = str(order_id or "")
    row["engine"] = str(engine or "")
    row["note"] = str(note or "")
    rows.append(row)


def _broker_fill_frames(df: pd.DataFrame, risk_fills_df: pd.DataFrame) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    base_df = df.copy()
    if "source" in base_df.columns and not risk_fills_df.empty:
        source_series = base_df["source"].astype(str).str.strip().str.lower()
        base_df = base_df.loc[~source_series.eq("risk")].copy()
    if not base_df.empty:
        frames.append(base_df)
    if not risk_fills_df.empty:
        frames.append(risk_fills_df.copy())
    return frames


def _execution_from_result(result: RunResult):
    payload = result.payload
    return payload.execution if isinstance(payload, (CurvePayload, AssessPayload)) else None


def _rows_from_broker_fills(result: RunResult) -> list[dict[str, Any]]:
    execution = _execution_from_result(result)
    if not isinstance(execution, LedgerExecutionArtifacts):
        return []
    frames = _broker_fill_frames(execution.fills_df, execution.risk_fills_df)
    if not frames:
        return []
    work = pd.concat(frames, ignore_index=True)
    if work.empty:
        return []
    work["_seq"] = pd.Series(range(len(work)), index=work.index, dtype="int64")
    if "trade_date" in work.columns:
        work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    if "order_id" in work.columns:
        order_num = work["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        work["_order_num"] = pd.to_numeric(order_num, errors="coerce")
    else:
        work["_order_num"] = pd.Series(float("nan"), index=work.index, dtype="float64")
    work = work.sort_values(["trade_date", "_order_num", "_seq"], kind="mergesort").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    for _, record in work.iterrows():
        slot_id = str(record.get("slot_id", "") or "").strip()
        source = str(record.get("source", "strategy") or "strategy").strip().lower()
        _append_row(
            rows,
            trade_date=record.get("trade_date"),
            portfolio=_slot_label(slot_id) if slot_id else "portfolio",
            stock_code=str(record.get("stock_code", "") or ""),
            side=str(record.get("side", "") or ""),
            qty=record.get("qty"),
            price=record.get("price"),
            cash_delta=record.get("cash_delta"),
            commission=record.get("commission"),
            stamp_tax=record.get("stamp_tax"),
            slippage_cost=record.get("slippage_cost"),
            total_cost=record.get("total_cost"),
            source=source,
            order_id=str(record.get("order_id", "") or ""),
            engine=result.request.engine_id or "broker",
            note=str(record.get("note", "") or ""),
        )
    return rows


def _rows_from_vectorized_frames(result: RunResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    execution = _execution_from_result(result)
    if not isinstance(execution, VectorizedExecutionArtifacts):
        return rows
    for slot_name, df in sorted(
        execution.processed_frames.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        required = {"trade_date", "stock_code", "buy_price", "sell_price"}
        if not required.issubset(df.columns):
            continue

        work = df.copy()
        work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
        work = work.dropna(subset=["trade_date"]).sort_values("trade_date", kind="mergesort").reset_index(drop=True)
        if work.empty:
            continue
        shares = (
            pd.to_numeric(work["shares"], errors="coerce").fillna(0.0)
            if "shares" in work.columns
            else None
        )
        prev_shares = shares.shift(1).fillna(0.0) if isinstance(shares, pd.Series) else None
        buy_price = pd.to_numeric(work["buy_price"], errors="coerce")
        sell_price = pd.to_numeric(work["sell_price"], errors="coerce")
        portfolio = _slot_label(slot_name)

        for idx, record in work.iterrows():
            has_buy = pd.notna(buy_price.iloc[idx])
            has_sell = pd.notna(sell_price.iloc[idx])
            if not has_buy and not has_sell:
                continue
            if isinstance(shares, pd.Series) and isinstance(prev_shares, pd.Series):
                buy_qty = max(float(shares.iloc[idx] - prev_shares.iloc[idx]), 0.0)
                sell_qty = max(float(prev_shares.iloc[idx] - shares.iloc[idx]), 0.0)
            else:
                buy_qty = None
                sell_qty = None
            if has_buy:
                _append_row(
                    rows,
                    trade_date=record.get("trade_date"),
                    portfolio=portfolio,
                    stock_code=str(record.get("stock_code", "") or ""),
                    side="buy",
                    qty=buy_qty,
                    price=buy_price.iloc[idx],
                    source="strategy",
                    engine="vectorized",
                    note="vectorized fill projection",
                )
            if has_sell:
                _append_row(
                    rows,
                    trade_date=record.get("trade_date"),
                    portfolio=portfolio,
                    stock_code=str(record.get("stock_code", "") or ""),
                    side="sell",
                    qty=sell_qty,
                    price=sell_price.iloc[idx],
                    source="strategy",
                    engine="vectorized",
                    note="vectorized fill projection",
                )
    return rows


def _ledger_meta(rows: list[dict[str, Any]], *, engine: str) -> dict[str, Any]:
    side_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for row in rows:
        side = str(row.get("side") or "").strip().lower()
        source = str(row.get("source") or "").strip().lower()
        if side:
            side_counts[side] = side_counts.get(side, 0) + 1
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "count": int(len(rows)),
        "engine": engine,
        "side_counts": dict(sorted(side_counts.items(), key=lambda item: str(item[0]).casefold())),
        "source_counts": dict(sorted(source_counts.items(), key=lambda item: str(item[0]).casefold())),
    }


def build_fills_ledger_payload(result: RunResult) -> dict[str, Any] | None:
    broker_rows = _rows_from_broker_fills(result)
    if broker_rows:
        rows = broker_rows
        engine = "broker"
    else:
        rows = _rows_from_vectorized_frames(result)
        engine = "vectorized"
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: (str(row.get("trade_date") or ""), str(row.get("portfolio") or "")))
    return {
        **artifact_schema_fields("fills_ledger", FILLS_LEDGER_SCHEMA_VERSION),
        "columns": list(FILLS_LEDGER_COLUMNS),
        "records": rows,
        "meta": _ledger_meta(rows, engine=engine),
    }


__all__ = ["FILLS_LEDGER_COLUMNS", "build_fills_ledger_payload"]

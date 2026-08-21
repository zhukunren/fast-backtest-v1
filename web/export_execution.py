"""Build normalized order and execution rows for web exports."""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.result import AssessPayload, CurvePayload, LedgerExecutionArtifacts, RunResult
from web.export_common import (
    EXECUTION_COLUMNS,
    ORDER_COLUMNS,
    _clean_float,
    _clean_int,
    _format_trade_date,
    _slot_label,
    _sorted_return_frames,
)

def _empty_execution_row() -> dict[str, Any]:
    return {column: None for column in EXECUTION_COLUMNS}


def _empty_order_row() -> dict[str, Any]:
    return {column: None for column in ORDER_COLUMNS}


def _append_execution_row(
    rows: list[dict[str, Any]],
    *,
    trade_date: Any,
    portfolio: str,
    stock_code: str = "",
    direction: str,
    shares: Any = None,
    price: Any = None,
    commission: Any = None,
    stamp_tax: Any = None,
    slippage_cost: Any = None,
    total_cost: Any = None,
    cash_delta: Any = None,
    source: str,
    before_ratio: Any = None,
    after_ratio: Any = None,
    risk_reason: str = "",
    risk_state: str = "",
    dd_state: str = "",
    note: str = "",
) -> None:
    row = _empty_execution_row()
    row["日期"] = _format_trade_date(trade_date)
    row["组合"] = str(portfolio or "")
    row["股票代码"] = str(stock_code or "")
    row["方向"] = str(direction or "")
    row["股数"] = _clean_int(shares)
    row["成交价"] = _clean_float(price, digits=4)
    row["手续费"] = _clean_float(commission, digits=6)
    row["印花税"] = _clean_float(stamp_tax, digits=6)
    row["滑点"] = _clean_float(slippage_cost, digits=6)
    row["手续费+印花税"] = _clean_float(total_cost, digits=6)
    row["资金变动"] = _clean_float(cash_delta, digits=6)
    row["来源"] = str(source or "")
    row["原仓位"] = _clean_float(before_ratio, digits=4)
    row["目标仓位"] = _clean_float(after_ratio, digits=4)
    row["风控原因"] = str(risk_reason or "")
    row["净值风控"] = str(risk_state or "")
    row["回撤风控"] = str(dd_state or "")
    row["备注"] = str(note or "")
    rows.append(row)


def _order_status_label(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "filled": "已成交",
        "rejected": "已拒绝",
        "skipped": "已跳过",
        "cancelled": "已取消",
        "canceled": "已取消",
    }
    return mapping.get(raw, str(value or ""))


def _append_order_row(
    rows: list[dict[str, Any]],
    *,
    trade_date: Any,
    portfolio: str,
    order_id: str,
    stock_code: str = "",
    direction: str,
    qty: Any = None,
    filled_qty: Any = None,
    price: Any = None,
    status: str = "",
    reason: str = "",
    source: str = "",
    exec_price_type: str = "",
    note: str = "",
) -> None:
    row = _empty_order_row()
    row["日期"] = _format_trade_date(trade_date)
    row["组合"] = str(portfolio or "")
    row["订单号"] = str(order_id or "")
    row["股票代码"] = str(stock_code or "")
    row["方向"] = str(direction or "")
    row["委托股数"] = _clean_int(qty)
    row["成交股数"] = _clean_int(filled_qty)
    row["价格"] = _clean_float(price, digits=4)
    row["状态"] = str(status or "")
    row["原因"] = str(reason or "")
    row["来源"] = str(source or "")
    row["价格类型"] = str(exec_price_type or "")
    row["备注"] = str(note or "")
    rows.append(row)


def _ledger_execution(result: RunResult) -> LedgerExecutionArtifacts | None:
    payload = result.payload
    execution = payload.execution if isinstance(payload, (CurvePayload, AssessPayload)) else None
    return execution if isinstance(execution, LedgerExecutionArtifacts) else None


def _build_execution_rows_from_broker_fills(result: RunResult) -> list[dict[str, Any]]:
    execution = _ledger_execution(result)
    if execution is None:
        return []

    rows: list[dict[str, Any]] = []
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
        return []

    work = pd.concat(frames, ignore_index=True)
    if "trade_date" in work.columns:
        work["_seq"] = pd.Series(range(len(work)), index=work.index, dtype="int64")
        if "order_id" in work.columns:
            order_num = work["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
            work["_order_num"] = pd.to_numeric(order_num, errors="coerce")
        else:
            work["_order_num"] = pd.Series(float("nan"), index=work.index, dtype="float64")
        work = work.sort_values(["trade_date", "_order_num", "_seq"], kind="mergesort").reset_index(drop=True)
    for _, record in work.iterrows():
        side = str(record.get("side", "")).strip().lower()
        direction = "买入" if side == "buy" else ("卖出" if side == "sell" else str(record.get("side", "") or ""))
        source_raw = str(record.get("source", "") or "").strip().lower()
        source = "风控" if source_raw == "risk" else "策略"
        _append_execution_row(
            rows,
            trade_date=record.get("trade_date"),
            portfolio=_slot_label(str(record.get("slot_id", "") or "")) if str(record.get("slot_id", "") or "").strip() else "组合",
            stock_code=str(record.get("stock_code", "") or ""),
            direction=direction,
            shares=record.get("qty"),
            price=record.get("price"),
            commission=record.get("commission"),
            stamp_tax=record.get("stamp_tax"),
            slippage_cost=record.get("slippage_cost"),
            total_cost=record.get("total_cost"),
            cash_delta=record.get("cash_delta"),
            source=source,
            before_ratio=record.get("before_ratio"),
            after_ratio=record.get("after_ratio"),
            risk_reason=str(record.get("risk_reason", "") or ""),
            risk_state=str(record.get("risk_state", "") or ""),
            dd_state=str(record.get("dd_state", "") or ""),
            note=str(record.get("note", "") or ""),
        )
    return rows


def _build_order_rows_from_portfolio_orders(result: RunResult) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    execution = _ledger_execution(result)
    if execution is None:
        return [], None

    frames: list[pd.DataFrame] = []
    base_df = execution.orders_df.copy()
    if "source" in base_df.columns and not execution.risk_orders_df.empty:
        source_series = base_df["source"].astype(str).str.strip().str.lower()
        base_df = base_df.loc[~source_series.eq("risk")].copy()
    frames.append(base_df)
    if not execution.risk_orders_df.empty:
        frames.append(execution.risk_orders_df.copy())
    if not any(isinstance(frame, pd.DataFrame) and not frame.empty for frame in frames):
        return [], None
    work = pd.concat(frames, ignore_index=True)
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
    status_counts = {
        "filled_count": 0,
        "rejected_count": 0,
        "skipped_count": 0,
        "cancelled_count": 0,
        "risk_count": 0,
    }
    for _, record in work.iterrows():
        side = str(record.get("side", "")).strip().lower()
        direction = "买入" if side == "buy" else ("卖出" if side == "sell" else str(record.get("side", "") or ""))
        source_raw = str(record.get("source", "") or "").strip().lower()
        source = "风控" if source_raw == "risk" else "策略"
        if source_raw == "risk":
            status_counts["risk_count"] += 1
        status_raw = str(record.get("status", "") or "").strip().lower()
        status = _order_status_label(status_raw)
        if status_raw == "filled":
            status_counts["filled_count"] += 1
        elif status_raw == "rejected":
            status_counts["rejected_count"] += 1
        elif status_raw == "skipped":
            status_counts["skipped_count"] += 1
        elif status_raw in {"cancelled", "canceled"}:
            status_counts["cancelled_count"] += 1
        _append_order_row(
            rows,
            trade_date=record.get("trade_date"),
            portfolio=_slot_label(str(record.get("slot_id", "") or "")) if str(record.get("slot_id", "") or "").strip() else "组合",
            order_id=str(record.get("order_id", "") or ""),
            stock_code=str(record.get("stock_code", "") or ""),
            direction=direction,
            qty=record.get("qty"),
            filled_qty=record.get("filled_qty", record.get("qty") if status_raw == "filled" else 0),
            price=record.get("price"),
            status=status,
            reason=str(record.get("reason", "") or ""),
            source=source,
            exec_price_type=str(record.get("exec_price_type", "") or ""),
            note=str(record.get("note", "") or ""),
        )
        rows[-1]["_order_num"] = record.get("_order_num")
        rows[-1]["_seq"] = record.get("_seq")
        rows[-1]["_status_raw"] = status_raw
        rows[-1]["_source_raw"] = source_raw
        rows[-1]["_side_raw"] = side

    def _order_row_sort_key(row: dict[str, Any]) -> tuple[object, float, int]:
        order_num = pd.to_numeric(row.get("_order_num"), errors="coerce")
        seq = pd.to_numeric(row.get("_seq"), errors="coerce")
        return (
            signal_sort_key(row.get("日期")),
            float(order_num) if pd.notna(order_num) else -1.0,
            int(seq) if pd.notna(seq) else -1,
        )

    rows = sorted(rows, key=_order_row_sort_key, reverse=True)
    for row in rows:
        row.pop("_order_num", None)
        row.pop("_seq", None)
    meta = {"count": len(rows)}
    meta.update(status_counts)
    return rows, meta


def _build_execution_rows_from_legacy_frames(result: RunResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot_name, df in _sorted_return_frames(result):
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        required = {"trade_date", "stock_code", "buy_price", "sell_price"}
        if not required.issubset(df.columns):
            continue

        work = df.copy()
        work = work.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
        shares = (
            pd.to_numeric(work["shares"], errors="coerce").fillna(0.0)
            if "shares" in work.columns
            else None
        )
        prev_shares = shares.shift(1).fillna(0.0) if isinstance(shares, pd.Series) else None
        buy_price = pd.to_numeric(work["buy_price"], errors="coerce")
        sell_price = pd.to_numeric(work["sell_price"], errors="coerce")
        commission = pd.to_numeric(work["commission"], errors="coerce") if "commission" in work.columns else pd.Series(0.0, index=work.index)
        stamp_tax = pd.to_numeric(work["stamp_tax"], errors="coerce") if "stamp_tax" in work.columns else pd.Series(0.0, index=work.index)
        slippage = pd.to_numeric(work["slippage_cost"], errors="coerce") if "slippage_cost" in work.columns else pd.Series(0.0, index=work.index)
        total_cost = pd.to_numeric(work["total_cost"], errors="coerce") if "total_cost" in work.columns else pd.Series(0.0, index=work.index)
        portfolio_label = _slot_label(slot_name)

        for idx, record in work.iterrows():
            has_buy = pd.notna(buy_price.iloc[idx])
            has_sell = pd.notna(sell_price.iloc[idx])
            if not has_buy and not has_sell:
                continue

            buy_qty = (
                max(float(shares.iloc[idx] - prev_shares.iloc[idx]), 0.0)
                if has_buy and isinstance(shares, pd.Series) and isinstance(prev_shares, pd.Series)
                else None
            )
            sell_qty = (
                max(float(prev_shares.iloc[idx] - shares.iloc[idx]), 0.0)
                if has_sell and isinstance(shares, pd.Series) and isinstance(prev_shares, pd.Series)
                else None
            )
            shared_cost_note = "；同日同时买卖，legacy 路径成本未拆分" if has_buy and has_sell else ""
            missing_detail_note = "" if "shares" in work.columns else "legacy 路径未提供股数与费用明细"
            combined_note = "；".join(part for part in (shared_cost_note.lstrip("；"), missing_detail_note) if part)

            if has_buy:
                _append_execution_row(
                    rows,
                    trade_date=record.get("trade_date"),
                    portfolio=portfolio_label,
                    stock_code=str(record.get("stock_code", "") or ""),
                    direction="买入",
                    shares=buy_qty,
                    price=buy_price.iloc[idx],
                    commission=None if has_sell or "commission" not in work.columns else commission.iloc[idx],
                    stamp_tax=None if has_sell or "stamp_tax" not in work.columns else stamp_tax.iloc[idx],
                    slippage_cost=None if has_sell or "slippage_cost" not in work.columns else slippage.iloc[idx],
                    total_cost=None if has_sell or "total_cost" not in work.columns else total_cost.iloc[idx],
                    source="策略",
                    note=combined_note,
                )
            if has_sell:
                _append_execution_row(
                    rows,
                    trade_date=record.get("trade_date"),
                    portfolio=portfolio_label,
                    stock_code=str(record.get("stock_code", "") or ""),
                    direction="卖出",
                    shares=sell_qty,
                    price=sell_price.iloc[idx],
                    commission=None if has_buy or "commission" not in work.columns else commission.iloc[idx],
                    stamp_tax=None if has_buy or "stamp_tax" not in work.columns else stamp_tax.iloc[idx],
                    slippage_cost=None if has_buy or "slippage_cost" not in work.columns else slippage.iloc[idx],
                    total_cost=None if has_buy or "total_cost" not in work.columns else total_cost.iloc[idx],
                    source="策略",
                    note=combined_note,
                )
    return rows


def _build_risk_adjustment_rows(result: RunResult) -> list[dict[str, Any]]:
    execution = _ledger_execution(result)
    if execution is not None:
        fills_df = execution.fills_df
        if not execution.risk_fills_df.empty:
            return []
        if "source" in fills_df.columns:
            source_series = fills_df["source"].astype(str).str.strip().str.lower()
            if source_series.eq("risk").any():
                return []
    payload = result.payload
    df = payload.equity_curve if isinstance(payload, CurvePayload) else None
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    if "trade_date" not in df.columns or "final_position_ratio" not in df.columns:
        return []

    work = df.copy()
    work = work.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    ratios = pd.to_numeric(work["final_position_ratio"], errors="coerce")
    if ratios.notna().sum() <= 1:
        return []

    rows: list[dict[str, Any]] = []
    for idx in range(1, len(work)):
        before_ratio = ratios.iloc[idx - 1]
        after_ratio = ratios.iloc[idx]
        if pd.isna(before_ratio) or pd.isna(after_ratio):
            continue
        if abs(float(after_ratio) - float(before_ratio)) <= 1e-9:
            continue

        if float(after_ratio) < float(before_ratio):
            direction = "减仓"
        elif abs(float(after_ratio) - 1.0) <= 1e-9:
            direction = "恢复"
        else:
            direction = "加仓"
        note_parts = [f"组合仓位 {float(before_ratio):.4f} -> {float(after_ratio):.4f}"]
        risk_state = str(work.iloc[idx].get("risk_state", "") or "").strip()
        dd_state = str(work.iloc[idx].get("dd_state", "") or "").strip()
        if float(after_ratio) > float(before_ratio):
            risk_reason = "风控恢复"
        elif risk_state and risk_state != "NORMAL" and dd_state and dd_state != "NORMAL":
            risk_reason = "净值/回撤风控触发"
        elif dd_state and dd_state != "NORMAL":
            risk_reason = "回撤风控触发"
        elif risk_state and risk_state != "NORMAL":
            risk_reason = "净值风控触发"
        else:
            risk_reason = "风控调仓"
        if risk_state:
            note_parts.append(f"净值风控 {risk_state}")
        if dd_state:
            note_parts.append(f"回撤风控 {dd_state}")

        _append_execution_row(
            rows,
            trade_date=work.iloc[idx].get("trade_date"),
            portfolio="组合",
            direction=direction,
            source="风控",
            before_ratio=before_ratio,
            after_ratio=after_ratio,
            risk_reason=risk_reason,
            risk_state=risk_state,
            dd_state=dd_state,
            note="；".join(note_parts),
        )
    return rows


def _build_execution_payload(result: RunResult) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    broker_rows = _build_execution_rows_from_broker_fills(result)
    fill_rows = broker_rows if broker_rows else _build_execution_rows_from_legacy_frames(result)
    risk_rows = _build_risk_adjustment_rows(result)
    all_rows = sorted(fill_rows + risk_rows, key=lambda row: signal_sort_key(row.get("日期")), reverse=True)
    if not all_rows:
        return [], None
    fill_count = sum(1 for row in fill_rows if str(row.get("来源", "")).strip() != "风控")
    risk_count = sum(1 for row in fill_rows if str(row.get("来源", "")).strip() == "风控") + len(risk_rows)
    meta = {
        "count": len(all_rows),
        "fill_count": fill_count,
        "risk_count": risk_count,
    }
    return all_rows, meta


def signal_sort_key(value: Any) -> str:
    text = _format_trade_date(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else text

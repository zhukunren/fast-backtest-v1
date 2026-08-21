"""
Net value merge and risk summary helpers.
"""
from __future__ import annotations

from typing import Dict, Literal

import numpy as np
import pandas as pd

from core.constants import COL_TRADE_DATE, POSITION_UTILIZATION_THRESHOLD
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.logging import log_info, log_warn

from ..evaluation.trade_segment_summary import (
    build_trade_segments_from_processed_dfs,
    build_trade_segments_summary_df,
)
from ..risk_control import apply_risk_control
from ..trade_calendar import normalize_trade_days_index
from .positions import build_position_and_returns_cash_result, build_position_and_returns_close_result
from .result import NetValueMergeResult, PositionAccountingResult

_UTILIZATION_COL_CANDIDATES = (
    "accounting_utilization_rate",
    "effective_position_ratio",
    "utilization_rate",
    "final_position_ratio",
    "position",
)


def _slot_initial_capital(cfg, *, spec=None) -> float:
    if spec is None:
        raise DataValidationError("_slot_initial_capital 需要显式传入 spec。")
    slots = int(spec.portfolio_num_j) * int(spec.portfolio_num_i)
    total = float(getattr(cfg.portfolio, "initial_capital", 1.0) or 1.0)
    if slots <= 0:
        slots = 1
    return total / slots


def _summarize_risk_control(name: str, df: pd.DataFrame) -> dict:
    out = {"portfolio": name}
    if "risk_state" in df.columns:
        state_counts = df["risk_state"].value_counts(dropna=False).to_dict()
        for key, val in state_counts.items():
            out[f"risk_state_{key}"] = int(val)
    if "dd_state" in df.columns:
        dd_counts = df["dd_state"].value_counts(dropna=False).to_dict()
        for key, val in dd_counts.items():
            out[f"dd_state_{key}"] = int(val)
    if "drawdown" in df.columns:
        out["max_drawdown"] = float(pd.to_numeric(df["drawdown"], errors="coerce").min())
    if "utilization_rate" in df.columns:
        out["avg_utilization"] = float(pd.to_numeric(df["utilization_rate"], errors="coerce").mean())
    return out


def _build_risk_summary_df(processed: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    if not processed:
        return None
    rows = []
    for name, df in processed.items():
        if df is None or df.empty:
            continue
        rows.append(_summarize_risk_control(name, df))
    if not rows:
        return None
    return pd.DataFrame(rows)


def _build_risk_events(name: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    if "trade_date" in df.columns:
        dates = ensure_trade_date_normalized(df, col="trade_date")["trade_date"]
    else:
        dates = pd.Series(pd.to_datetime(df.index, errors="raise").normalize())
    util_col = None
    for col in ("effective_position_ratio", "utilization_rate", "final_position_ratio", "position"):
        if col in df.columns:
            util_col = col
            break
    if util_col is None:
        return []
    ratios = pd.to_numeric(df[util_col], errors="coerce").fillna(1.0).to_numpy(dtype="float64")
    active = ratios < POSITION_UTILIZATION_THRESHOLD
    if not active.any():
        return []

    risk_state = df["risk_state"].astype(str).to_numpy() if "risk_state" in df.columns else None
    dd_state = df["dd_state"].astype(str).to_numpy() if "dd_state" in df.columns else None
    drawdown = pd.to_numeric(df["drawdown"], errors="coerce").to_numpy() if "drawdown" in df.columns else None

    rows: list[dict] = []
    start_idx = None
    for i in range(len(active)):
        if active[i] and start_idx is None:
            start_idx = i
            continue
        if (not active[i]) and (start_idx is not None):
            end_idx = i - 1
            row = {
                "portfolio": name,
                "trigger_date": dates.iloc[start_idx],
                "end_date": dates.iloc[end_idx],
                "duration_days": int(end_idx - start_idx + 1),
                "recovery_date": dates.iloc[i],
            }
            if risk_state is not None:
                row["trigger_risk_state"] = risk_state[start_idx]
            if dd_state is not None:
                row["trigger_dd_state"] = dd_state[start_idx]
            if drawdown is not None:
                row["min_drawdown"] = float(np.nanmin(drawdown[start_idx : end_idx + 1]))
            rows.append(row)
            start_idx = None

    if start_idx is not None:
        end_idx = len(active) - 1
        row = {
            "portfolio": name,
            "trigger_date": dates.iloc[start_idx],
            "end_date": dates.iloc[end_idx],
            "duration_days": int(end_idx - start_idx + 1),
            "recovery_date": None,
        }
        if risk_state is not None:
            row["trigger_risk_state"] = risk_state[start_idx]
        if dd_state is not None:
            row["trigger_dd_state"] = dd_state[start_idx]
        if drawdown is not None:
            row["min_drawdown"] = float(np.nanmin(drawdown[start_idx : end_idx + 1]))
        rows.append(row)

    return rows


def _build_risk_events_df(processed: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    if not processed:
        return None
    rows: list[dict] = []
    for name, df in processed.items():
        rows.extend(_build_risk_events(name, df))
    if not rows:
        return None
    return pd.DataFrame(rows)


def _build_cost_summary_df(
    accounting_results: dict[str, PositionAccountingResult],
) -> pd.DataFrame | None:
    cost_rows = []
    for name, result in accounting_results.items():
        summary = result.cost_summary
        if not summary:
            continue
        row = {"portfolio": name}
        row.update(summary)
        cost_rows.append(row)
    if not cost_rows:
        return None
    cost_rows = sorted(cost_rows, key=lambda r: str(r["portfolio"]).casefold())
    cost_df = pd.DataFrame(cost_rows)
    cost_df["cost_drag_pct"] = pd.to_numeric(cost_df["cost_drag"], errors="coerce") * 100.0
    cols = [
        "portfolio",
        "buy_cnt",
        "sell_cnt",
        "net_value_pre_cost",
        "net_value_post_cost",
        "cost_drag_pct",
        "cost_rate_sum",
    ]
    return cost_df.loc[:, [c for c in cols if c in cost_df.columns]]


def _resolve_utilization_col(df: pd.DataFrame) -> str | None:
    for col in _UTILIZATION_COL_CANDIDATES:
        if col in df.columns:
            return col
    return None


def _prepare_merge_source_frame(name: str, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df is None or df.empty:
        raise DataValidationError(f"{name}: processed df 为空。")
    if COL_TRADE_DATE not in df.columns or "net_value" not in df.columns:
        raise DataValidationError(f"{name}: 缺少 trade_date/net_value 列，无法合并净值。")

    util_col = _resolve_utilization_col(df)
    use_cols = [COL_TRADE_DATE, "net_value"]
    if util_col is not None:
        use_cols.append(util_col)

    temp = df.loc[:, use_cols].copy()
    # Drop attrs so later pd.concat() does not compare DataFrame-valued attrs
    # copied from slot_broker processed frames (risk fills/orders).
    temp.attrs = {}
    temp = ensure_trade_date_normalized(temp, col=COL_TRADE_DATE)
    temp["net_value"] = pd.to_numeric(temp["net_value"], errors="raise").ffill().fillna(1.0)
    if util_col is None:
        temp["utilization_rate"] = 0.0
    else:
        temp["utilization_rate"] = pd.to_numeric(temp[util_col], errors="coerce").fillna(0.0)
    temp["position_value"] = temp["net_value"] * temp["utilization_rate"]
    temp = temp.set_index(COL_TRADE_DATE)

    net_df = temp.loc[:, ["net_value"]].rename(columns={"net_value": f"{name}_with_returns_net_value"})
    pos_df = temp.loc[:, ["position_value"]].rename(columns={"position_value": f"{name}_position_value"})
    net_df.attrs = {}
    pos_df.attrs = {}
    return temp, net_df, pos_df


def _build_single_merged_df(name: str, df: pd.DataFrame) -> pd.DataFrame:
    temp, net_df, _pos_df = _prepare_merge_source_frame(name, df)
    net_col = str(net_df.columns[0])
    out = net_df.copy()
    utilization = temp["utilization_rate"].to_numpy(dtype="float64", copy=False)
    position_value = temp["position_value"].to_numpy(dtype="float64", copy=False)
    out["avg_net_value"] = out[net_col].to_numpy(dtype="float64", copy=False)
    out["sum_net_value"] = out[net_col].to_numpy(dtype="float64", copy=False)
    out["utilization_rate"] = utilization
    out["avg_position_value"] = position_value
    out["position_value"] = position_value
    return out.reset_index()


def _process_trading_frames(
    trading_dfs: Dict[str, pd.DataFrame],
    *,
    quote_store,
    cfg,
    spec,
    execution_policy,
    risk_cfg,
    accounting_mode: Literal["close", "cash"],
    net_value_runtime=None,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    dict[str, PositionAccountingResult],
]:
    processed: dict[str, pd.DataFrame] = {}
    raw_processed: dict[str, pd.DataFrame] = {}
    accounting_results: dict[str, PositionAccountingResult] = {}
    if accounting_mode not in {"close", "cash"}:
        raise DataValidationError(f"unsupported net-value accounting mode: {accounting_mode!r}")
    cash_accounting = accounting_mode == "cash"
    for name, df in trading_dfs.items():
        if df is None or df.empty:
            raise DataValidationError(f"{name}: trading_df 为空，无法计算净值。")
        if cash_accounting:
            accounting_result = build_position_and_returns_cash_result(
                df,
                initial_capital=_slot_initial_capital(cfg, spec=spec),
                portfolio_initial_capital=float(getattr(cfg.portfolio, "initial_capital", 1.0) or 1.0),
                execution_policy=execution_policy,
                cfg=cfg,
                quote_store=quote_store,
            )
        else:
            accounting_result = build_position_and_returns_close_result(
                df,
                execution_policy=execution_policy,
                cfg=cfg,
                runtime=net_value_runtime,
            )
        processed_df = accounting_result.frame
        accounting_results[name] = accounting_result
        raw_processed[name] = processed_df
        if risk_cfg.enabled:
            processed_df = apply_risk_control(
                processed_df,
                allow_intraday=bool(execution_policy.allow_intraday),
                execution_policy=execution_policy,
                enable_nv=risk_cfg.enable_nv,
                enable_dd=risk_cfg.enable_dd,
                warn_dd=risk_cfg.warn_dd,
                danger_dd=risk_cfg.danger_dd,
                lock_dd=risk_cfg.lock_dd,
                nv_warn=risk_cfg.nv_warn,
                nv_danger=risk_cfg.nv_danger,
                nv_clear=risk_cfg.nv_clear,
                nv_ratios=risk_cfg.nv_ratios,
                dd_ratios=risk_cfg.dd_ratios,
                daily_return_scope="portfolio" if cash_accounting else "invested",
                portfolio_position_ratio=(
                    pd.to_numeric(
                        processed_df["utilization_rate"],
                        errors="raise",
                    ).to_numpy(dtype="float64", copy=False)
                    if cash_accounting
                    else None
                ),
            )
        processed[name] = processed_df
    return processed, raw_processed, accounting_results


def _align_to_trade_calendar(df: pd.DataFrame, days_idx: pd.DatetimeIndex) -> pd.DataFrame:
    if df is None or df.empty or COL_TRADE_DATE not in df.columns:
        return df
    temp = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
    if not temp[COL_TRADE_DATE].is_monotonic_increasing:
        temp = temp.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
    elif not isinstance(temp.index, pd.RangeIndex):
        temp = temp.reset_index(drop=True)
    if temp[COL_TRADE_DATE].duplicated().any():
        duplicate_count = int(temp[COL_TRADE_DATE].duplicated().sum())
        sample = temp.loc[temp[COL_TRADE_DATE].duplicated(), [COL_TRADE_DATE]].head(5).to_dict(
            orient="records"
        )
        log_warn(
            f"交易日历对齐：发现 trade_date 重复 {duplicate_count}，已保留最后一条，sample={sample}"
        )
        temp = temp.drop_duplicates(subset=[COL_TRADE_DATE], keep="last").reset_index(drop=True)
    temp = temp.set_index(COL_TRADE_DATE).reindex(days_idx).ffill()
    temp.index.name = COL_TRADE_DATE
    return temp.reset_index()


def _validate_processed_dates(
    processed: dict[str, pd.DataFrame],
    *,
    trade_days_idx: pd.DatetimeIndex,
) -> None:
    for name, df in processed.items():
        if df is None or df.empty or COL_TRADE_DATE not in df.columns:
            continue
        normalized = ensure_trade_date_normalized(df, col=COL_TRADE_DATE)
        series = normalized[COL_TRADE_DATE]
        if series.isna().any():
            raise DataValidationError(f"{name}: processed.trade_date 存在 NaT，无法对齐净值窗口。")
        missing_days = pd.DatetimeIndex(sorted(series.unique())).difference(trade_days_idx)
        if len(missing_days) > 0:
            sample = [pd.Timestamp(day).strftime("%Y-%m-%d") for day in missing_days[:5]]
            raise DataValidationError(f"{name}: processed.trade_date 存在不在 trade_days 中的日期：{sample}")


def _strategy_fills_without_costs(
    slot_fills: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    no_cost: dict[str, pd.DataFrame] = {}
    for slot_id, frame in sorted(slot_fills.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            no_cost[slot_id] = pd.DataFrame()
            continue
        out = frame.copy()
        qty = pd.to_numeric(out["qty"], errors="raise").astype(int)
        exec_price = pd.to_numeric(out["price"], errors="raise")
        slippage_cost = (
            pd.to_numeric(out["slippage_cost"], errors="coerce").fillna(0.0)
            if "slippage_cost" in out.columns
            else pd.Series(0.0, index=out.index, dtype="float64")
        )
        per_share_slippage = slippage_cost / qty.where(qty > 0, 1)
        side = out["side"].astype(str).str.strip().str.lower()
        raw_price = exec_price.copy()
        raw_price.loc[side.eq("buy")] -= per_share_slippage.loc[side.eq("buy")]
        raw_price.loc[side.eq("sell")] += per_share_slippage.loc[side.eq("sell")]
        notional = qty * raw_price
        out["price"] = raw_price
        out["commission"] = 0.0
        out["stamp_tax"] = 0.0
        out["slippage_cost"] = 0.0
        out["total_cost"] = 0.0
        out["cash_delta"] = 0.0
        out.loc[side.eq("buy"), "cash_delta"] = -notional.loc[side.eq("buy")]
        out.loc[side.eq("sell"), "cash_delta"] = notional.loc[side.eq("sell")]
        no_cost[slot_id] = out
    return no_cost


def _build_slot_broker_artifacts(
    *,
    collect_ledger_artifacts: bool,
    build_risk_accounting: bool,
    cfg,
    spec,
    raw_processed: dict[str, pd.DataFrame],
    risk_processed: dict[str, pd.DataFrame],
    accounting_results: dict[str, PositionAccountingResult],
    quote_store,
) -> tuple[
    pd.DataFrame | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
]:
    if not collect_ledger_artifacts and not build_risk_accounting:
        return None, None, None, None, None, {}, {}
    from ..slot_broker import (
        build_slot_broker_orders_and_fills,
        build_slot_broker_risk_orders,
    )
    from ..slot_broker_risk_fills import build_slot_broker_risk_fill_result
    from .cash_quotes import build_cash_quote_resolver

    slot_orders = {
        name: result.orders_df
        for name, result in accounting_results.items()
        if isinstance(result.orders_df, pd.DataFrame)
    }
    slot_fills = {
        name: result.fills_df
        for name, result in accounting_results.items()
        if isinstance(result.fills_df, pd.DataFrame)
    }
    if collect_ledger_artifacts:
        orders_df, fills_df = build_slot_broker_orders_and_fills(
            slot_orders=slot_orders,
            slot_fills=slot_fills,
        )
    else:
        orders_df, fills_df = None, None
    strategy_fills_for_risk = slot_fills
    if isinstance(fills_df, pd.DataFrame) and not fills_df.empty and "slot_id" in fills_df.columns:
        strategy_fills_for_risk = {
            str(slot_id): frame.reset_index(drop=True)
            for slot_id, frame in fills_df.groupby("slot_id", sort=False)
        }
    starting_order_index = (len(fills_df) + 1) if isinstance(fills_df, pd.DataFrame) else 1
    quote_resolver = build_cash_quote_resolver(quote_store)
    risk_result = build_slot_broker_risk_fill_result(
        raw_processed_dfs=raw_processed,
        risk_processed_dfs=risk_processed,
        strategy_fills_dfs=strategy_fills_for_risk,
        initial_slot_capital=_slot_initial_capital(cfg, spec=spec),
        lot_size=max(int(getattr(cfg.portfolio, "lot_size", 1) or 1), 1),
        allow_partial_fill=bool(getattr(cfg.portfolio, "allow_partial_fill", True)),
        cost_cfg=cfg.transaction_cost,
        strategy_cost_cfg=cfg.transaction_cost,
        starting_order_index=starting_order_index,
        trade_constraints=cfg.trade_constraints,
        quote_resolver=quote_resolver,
    )
    no_cost_daily: dict[str, pd.DataFrame] = {}
    if build_risk_accounting:
        no_cost_strategy_fills = _strategy_fills_without_costs(strategy_fills_for_risk)
        no_cost_result = build_slot_broker_risk_fill_result(
            raw_processed_dfs=raw_processed,
            risk_processed_dfs=risk_processed,
            strategy_fills_dfs=no_cost_strategy_fills,
            initial_slot_capital=_slot_initial_capital(cfg, spec=spec),
            lot_size=max(int(getattr(cfg.portfolio, "lot_size", 1) or 1), 1),
            allow_partial_fill=bool(getattr(cfg.portfolio, "allow_partial_fill", True)),
            cost_cfg=None,
            strategy_cost_cfg=None,
            starting_order_index=starting_order_index,
            trade_constraints=cfg.trade_constraints,
            quote_resolver=quote_resolver,
        )
        no_cost_daily = no_cost_result.daily_accounting_by_slot
    if not collect_ledger_artifacts:
        return None, None, None, None, None, risk_result.daily_accounting_by_slot, no_cost_daily
    if not isinstance(fills_df, pd.DataFrame):
        fills_df = pd.DataFrame()
    if not isinstance(orders_df, pd.DataFrame):
        orders_df = pd.DataFrame()
    if build_risk_accounting:
        fills_df = risk_result.strategy_fills_df
    risk_fills_df = risk_result.fills_df
    risk_orders_df = build_slot_broker_risk_orders(risk_fills_df)
    return (
        orders_df,
        fills_df,
        risk_orders_df,
        risk_fills_df,
        risk_result.strategy_rejections_df,
        risk_result.daily_accounting_by_slot,
        no_cost_daily,
    )


def _apply_slot_risk_accounting(
    processed: dict[str, pd.DataFrame],
    *,
    daily_accounting_by_slot: dict[str, pd.DataFrame],
    initial_slot_capital: float,
) -> None:
    risk_columns = (
        "risk_fill_count",
        "risk_buy_count",
        "risk_sell_count",
        "risk_commission",
        "risk_stamp_tax",
        "risk_slippage_cost",
        "risk_fee_cost",
        "risk_total_cost",
    )
    for slot_id, source in sorted(processed.items(), key=lambda item: str(item[0]).casefold()):
        accounting = daily_accounting_by_slot.get(slot_id)
        if not isinstance(accounting, pd.DataFrame) or accounting.empty:
            raise DataValidationError(f"{slot_id}: slot 风控影子账本缺失。")
        out = ensure_trade_date_normalized(source.copy(), col=COL_TRADE_DATE)
        out = out.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
        accounting = ensure_trade_date_normalized(accounting.copy(), col=COL_TRADE_DATE)
        accounting = accounting.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)
        if len(out) != len(accounting) or not out[COL_TRADE_DATE].equals(accounting[COL_TRADE_DATE]):
            raise DataValidationError(f"{slot_id}: slot 风控影子账本日期与 processed frame 不一致。")

        raw_aliases = {
            "cash_raw": "cash",
            "shares_raw": "shares",
            "position_value_abs_raw": "position_value_abs",
            "equity_raw": "equity",
        }
        for raw_name, source_name in raw_aliases.items():
            if raw_name not in out.columns and source_name in out.columns:
                out[raw_name] = out[source_name]
        out["net_value_risk_model"] = pd.to_numeric(out["net_value"], errors="raise")
        if "daily_return" in out.columns:
            model_daily_return = pd.to_numeric(out["daily_return"], errors="coerce").fillna(0.0)
        else:
            model_daily_return = out["net_value_risk_model"].pct_change().fillna(
                out["net_value_risk_model"] - 1.0
            )
        out["daily_return_risk_model"] = model_daily_return
        out["equity_risk_model"] = out["net_value_risk_model"] * float(initial_slot_capital)

        for column in ("cash", "shares", "position_value_abs", "equity", "net_value", "daily_return", "utilization_rate"):
            out[column] = pd.to_numeric(accounting[column], errors="raise").to_numpy(copy=False)
        for column in risk_columns:
            out[column] = pd.to_numeric(accounting[column], errors="raise").to_numpy(copy=False)
        for column in ("strategy_buy_count", "strategy_sell_count"):
            out[column] = pd.to_numeric(accounting[column], errors="raise").to_numpy(copy=False)
        out["buy_count"] = out["strategy_buy_count"] + out["risk_buy_count"]
        out["sell_count"] = out["strategy_sell_count"] + out["risk_sell_count"]

        strategy_cost_columns = (
            ("commission", "strategy_commission", "risk_commission"),
            ("stamp_tax", "strategy_stamp_tax", "risk_stamp_tax"),
            ("slippage_cost", "strategy_slippage_cost", "risk_slippage_cost"),
            ("total_cost", "strategy_fee_cost", "risk_fee_cost"),
        )
        for column, strategy_column, risk_column in strategy_cost_columns:
            base = pd.to_numeric(accounting[strategy_column], errors="raise")
            out[strategy_column] = base
            out[column] = base + pd.to_numeric(out[risk_column], errors="raise")
        out["strategy_total_cost"] = pd.to_numeric(
            accounting["strategy_total_cost"],
            errors="raise",
        )

        out["shares"] = pd.to_numeric(out["shares"], errors="raise").astype(int)
        out["position"] = (out["shares"] > 0).astype(int)
        out["position_value"] = out["position_value_abs"] / float(initial_slot_capital)
        out["accounting_utilization_rate"] = out["utilization_rate"]
        out["equity_final"] = out["net_value"]
        out["cash_equity"] = out["cash"] / float(initial_slot_capital)
        out["invested_equity"] = out["position_value_abs"] / float(initial_slot_capital)
        peak = out["net_value"].cummax()
        out["drawdown"] = out["net_value"] / peak - 1.0
        processed[slot_id] = out


def _augment_cost_summary_with_risk(
    cost_df: pd.DataFrame | None,
    *,
    processed: dict[str, pd.DataFrame],
    no_cost_daily_by_slot: dict[str, pd.DataFrame],
    initial_slot_capital: float,
) -> pd.DataFrame | None:
    if not no_cost_daily_by_slot:
        return cost_df
    rows_by_slot: dict[str, dict[str, object]] = {}
    if isinstance(cost_df, pd.DataFrame) and not cost_df.empty:
        for _, row in cost_df.iterrows():
            rows_by_slot[str(row["portfolio"])] = row.to_dict()

    for slot_id, frame in sorted(processed.items(), key=lambda item: str(item[0]).casefold()):
        no_cost_daily = no_cost_daily_by_slot.get(slot_id)
        if not isinstance(no_cost_daily, pd.DataFrame) or no_cost_daily.empty:
            raise DataValidationError(f"{slot_id}: slot 无风险调仓成本对照账本缺失。")
        row = rows_by_slot.get(slot_id, {"portfolio": slot_id})
        for legacy_column in (
            "buy_cnt",
            "sell_cnt",
            "net_value_pre_cost",
            "net_value_post_cost",
            "cost_drag_pct",
            "cost_rate_sum",
        ):
            row[f"raw_strategy_{legacy_column}"] = row.get(legacy_column, np.nan)
        risk_pre = float(pd.to_numeric(no_cost_daily["net_value"], errors="raise").iloc[-1])
        risk_post = float(pd.to_numeric(frame["net_value"], errors="raise").iloc[-1])
        risk_total = float(pd.to_numeric(frame["risk_total_cost"], errors="coerce").fillna(0.0).sum())
        commission = float(pd.to_numeric(frame["commission"], errors="coerce").fillna(0.0).sum())
        stamp_tax = float(pd.to_numeric(frame["stamp_tax"], errors="coerce").fillna(0.0).sum())
        slippage_cost = float(pd.to_numeric(frame["slippage_cost"], errors="coerce").fillna(0.0).sum())
        fee_cost = float(pd.to_numeric(frame["total_cost"], errors="coerce").fillna(0.0).sum())
        strategy_slippage = float(
            pd.to_numeric(frame["strategy_slippage_cost"], errors="coerce").fillna(0.0).sum()
        )
        strategy_fee = float(pd.to_numeric(frame["strategy_fee_cost"], errors="coerce").fillna(0.0).sum())
        strategy_total = float(
            pd.to_numeric(frame["strategy_total_cost"], errors="coerce").fillna(0.0).sum()
        )
        row["buy_cnt"] = int(pd.to_numeric(frame["buy_count"], errors="coerce").fillna(0).sum())
        row["sell_cnt"] = int(pd.to_numeric(frame["sell_count"], errors="coerce").fillna(0).sum())
        row["risk_buy_cnt"] = int(pd.to_numeric(frame["risk_buy_count"], errors="coerce").fillna(0).sum())
        row["risk_sell_cnt"] = int(pd.to_numeric(frame["risk_sell_count"], errors="coerce").fillna(0).sum())
        row["risk_commission"] = float(pd.to_numeric(frame["risk_commission"], errors="coerce").fillna(0.0).sum())
        row["risk_stamp_tax"] = float(pd.to_numeric(frame["risk_stamp_tax"], errors="coerce").fillna(0.0).sum())
        row["risk_slippage_cost"] = float(
            pd.to_numeric(frame["risk_slippage_cost"], errors="coerce").fillna(0.0).sum()
        )
        row["risk_fee_cost"] = float(pd.to_numeric(frame["risk_fee_cost"], errors="coerce").fillna(0.0).sum())
        row["risk_total_cost"] = risk_total
        row["risk_net_value_pre_cost"] = risk_pre
        row["risk_net_value_post_cost"] = risk_post
        row["risk_cost_drag_pct"] = (1.0 - risk_post / risk_pre) * 100.0 if risk_pre else np.nan
        row["risk_cost_rate_sum"] = risk_total / float(initial_slot_capital)
        row["net_value_pre_cost"] = risk_pre
        row["net_value_post_cost"] = risk_post
        row["cost_drag_pct"] = row["risk_cost_drag_pct"]
        row["commission"] = commission
        row["stamp_tax"] = stamp_tax
        row["slippage_cost"] = slippage_cost
        row["fee_cost"] = fee_cost
        row["total_cost"] = fee_cost + slippage_cost
        row["strategy_fee_cost"] = strategy_fee
        row["strategy_slippage_cost"] = strategy_slippage
        row["strategy_total_cost"] = strategy_total
        row["cost_rate_sum"] = row["total_cost"] / float(initial_slot_capital)
        rows_by_slot[slot_id] = row

    if not rows_by_slot:
        return cost_df
    out = pd.DataFrame(
        [rows_by_slot[key] for key in sorted(rows_by_slot, key=str.casefold)]
    )
    preferred = [
        "portfolio",
        "raw_strategy_buy_cnt",
        "raw_strategy_sell_cnt",
        "buy_cnt",
        "sell_cnt",
        "risk_buy_cnt",
        "risk_sell_cnt",
        "net_value_pre_cost",
        "raw_strategy_net_value_pre_cost",
        "raw_strategy_net_value_post_cost",
        "risk_net_value_pre_cost",
        "net_value_post_cost",
        "cost_drag_pct",
        "raw_strategy_cost_drag_pct",
        "risk_cost_drag_pct",
        "cost_rate_sum",
        "raw_strategy_cost_rate_sum",
        "risk_cost_rate_sum",
        "commission",
        "stamp_tax",
        "slippage_cost",
        "fee_cost",
        "total_cost",
        "strategy_fee_cost",
        "strategy_slippage_cost",
        "strategy_total_cost",
        "risk_commission",
        "risk_stamp_tax",
        "risk_slippage_cost",
        "risk_fee_cost",
        "risk_total_cost",
    ]
    trailing = [column for column in out.columns if column not in preferred]
    return out.loc[:, [column for column in preferred if column in out.columns] + trailing]


def _log_cost_summary(cost_df: pd.DataFrame | None, *, quiet: bool) -> None:
    if cost_df is None or cost_df.empty:
        return
    if not quiet:
        log_info("[COST_SUMMARY] per-portfolio trade counts and cost impact:")
        log_info(cost_df.to_string(index=False))
    total_buy = int(pd.to_numeric(cost_df["buy_cnt"], errors="coerce").fillna(0).sum())
    total_sell = int(pd.to_numeric(cost_df["sell_cnt"], errors="coerce").fillna(0).sum())
    avg_pre = float(pd.to_numeric(cost_df["net_value_pre_cost"], errors="coerce").mean())
    avg_post = float(pd.to_numeric(cost_df["net_value_post_cost"], errors="coerce").mean())
    total_drag = np.nan
    if np.isfinite(avg_pre) and avg_pre and np.isfinite(avg_post):
        total_drag = 1.0 - (avg_post / avg_pre)
    if not quiet:
        drag_pct = f"{total_drag * 100:.2f}%" if np.isfinite(total_drag) else "nan"
        log_info(
            "[COST_SUMMARY] total_buy="
            f"{total_buy} total_sell={total_sell} avg_pre_nv={avg_pre:.6f} "
            f"avg_post_nv={avg_post:.6f} cost_drag={drag_pct}"
        )


def _merge_multiple_sources(merge_sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    net_frames: list[pd.DataFrame] = []
    position_frames: list[pd.DataFrame] = []
    for name, df in merge_sources.items():
        _temp, net_df, position_df = _prepare_merge_source_frame(name, df)
        net_frames.append(net_df)
        position_frames.append(position_df)
    if not net_frames:
        raise DataValidationError("dfs 为空，无法合并净值。")
    merged = pd.concat(net_frames, axis=1, sort=True).sort_index()
    value_cols = list(merged.columns)
    merged[value_cols] = merged[value_cols].ffill().fillna(1.0)
    avg_net_value = merged[value_cols].mean(axis=1, skipna=False)
    merged["avg_net_value"] = avg_net_value
    merged["sum_net_value"] = avg_net_value
    if position_frames:
        position_df = pd.concat(position_frames, axis=1, sort=True).sort_index()
        position_cols = list(position_df.columns)
        position_df = position_df.reindex(merged.index).ffill().fillna(0.0)
        sum_net_value = merged[value_cols].sum(axis=1, skipna=False)
        sum_position = position_df[position_cols].sum(axis=1, skipna=False)
        merged["utilization_rate"] = (
            sum_position / sum_net_value.replace(0, np.nan)
        ).fillna(0.0).to_numpy()
        avg_position_value = (sum_position / len(position_cols)).to_numpy()
        merged["avg_position_value"] = avg_position_value
        merged["position_value"] = avg_position_value
    return merged.reset_index().rename(columns={"index": "trade_date"})


def _build_merge_result(
    *,
    merged_df: pd.DataFrame,
    processed: dict[str, pd.DataFrame],
    trade_segments_df: pd.DataFrame,
    trade_segments_summary_df: pd.DataFrame,
    risk_summary_df: pd.DataFrame | None,
    risk_events_df: pd.DataFrame | None,
    cost_df: pd.DataFrame | None,
    portfolio_orders_df: pd.DataFrame | None,
    portfolio_fills_df: pd.DataFrame | None,
    risk_orders_df: pd.DataFrame | None,
    risk_fills_df: pd.DataFrame | None,
    strategy_rejections_df: pd.DataFrame | None,
) -> NetValueMergeResult:
    return NetValueMergeResult(
        merged_df=merged_df,
        processed_dfs=dict(processed),
        trade_segments_df=trade_segments_df,
        trade_segments_summary_df=trade_segments_summary_df,
        risk_control_summary_df=risk_summary_df,
        risk_control_events_df=risk_events_df,
        cost_summary_df=cost_df,
        portfolio_orders_df=portfolio_orders_df,
        portfolio_fills_df=portfolio_fills_df,
        risk_orders_df=risk_orders_df,
        risk_fills_df=risk_fills_df,
        strategy_rejections_df=strategy_rejections_df,
    )


def merge_net_value_result_from_trading_dfs(
    trading_dfs: Dict[str, pd.DataFrame],
    *,
    accounting_mode: Literal["close", "cash"],
    collect_ledger_artifacts: bool = False,
    quote_store=None,
    trade_days: pd.DatetimeIndex | list[pd.Timestamp] | pd.DataFrame | None = None,
    execution_policy=None,
    cfg=None,
    spec=None,
    risk_cfg=None,
    net_value_runtime=None,
) -> NetValueMergeResult:
    if cfg is None or spec is None or execution_policy is None:
        raise DataValidationError(
            "merge_net_value_result_from_trading_dfs 需要显式传入 cfg/spec/execution_policy。"
        )
    if not trading_dfs:
        raise DataValidationError("trading_dfs 为空，无法合并净值。")
    if risk_cfg is None:
        raise DataValidationError("merge_net_value_result_from_trading_dfs 需要显式 risk_cfg。")
    if trade_days is None:
        raise DataValidationError("merge_net_value_result_from_trading_dfs 需要显式传入 trade_days。")

    processed, raw_processed, accounting_results = _process_trading_frames(
        trading_dfs,
        quote_store=quote_store,
        cfg=cfg,
        spec=spec,
        execution_policy=execution_policy,
        risk_cfg=risk_cfg,
        accounting_mode=accounting_mode,
        net_value_runtime=net_value_runtime,
    )
    if not processed:
        raise DataValidationError("processed 为空，无法合并净值。")
    trade_segments_df = build_trade_segments_from_processed_dfs(raw_processed)
    trade_segments_summary_df = build_trade_segments_summary_df(trade_segments_df, prefix="实际交易段")
    trade_days_idx = normalize_trade_days_index(trade_days, name="trade_days")
    _validate_processed_dates(processed, trade_days_idx=trade_days_idx)
    build_risk_accounting = (
        accounting_mode == "cash"
        and bool(risk_cfg.enabled)
    )
    (
        portfolio_orders_df,
        portfolio_fills_df,
        risk_orders_df,
        risk_fills_df,
        strategy_rejections_df,
        risk_daily_by_slot,
        no_cost_risk_daily_by_slot,
    ) = _build_slot_broker_artifacts(
        collect_ledger_artifacts=collect_ledger_artifacts,
        build_risk_accounting=build_risk_accounting,
        cfg=cfg,
        spec=spec,
        raw_processed=raw_processed,
        risk_processed=processed,
        accounting_results=accounting_results,
        quote_store=quote_store,
    )
    initial_slot_capital = _slot_initial_capital(cfg, spec=spec)
    if build_risk_accounting:
        _apply_slot_risk_accounting(
            processed,
            daily_accounting_by_slot=risk_daily_by_slot,
            initial_slot_capital=initial_slot_capital,
        )
        _validate_processed_dates(processed, trade_days_idx=trade_days_idx)
    risk_summary_df = _build_risk_summary_df(processed) if risk_cfg.enabled else None
    risk_events_df = _build_risk_events_df(processed) if risk_cfg.enabled else None
    merge_sources = {
        name: _align_to_trade_calendar(df, trade_days_idx)
        for name, df in processed.items()
    }
    cost_df = _build_cost_summary_df(accounting_results)
    if build_risk_accounting:
        cost_df = _augment_cost_summary_with_risk(
            cost_df,
            processed=processed,
            no_cost_daily_by_slot=no_cost_risk_daily_by_slot,
            initial_slot_capital=initial_slot_capital,
        )
    _log_cost_summary(cost_df, quiet=bool(cfg.run.quiet))
    if len(merge_sources) == 1:
        name, df = next(iter(merge_sources.items()))
        merged_df = _build_single_merged_df(name, df)
    else:
        merged_df = _merge_multiple_sources(merge_sources)
    return _build_merge_result(
        merged_df=merged_df,
        processed=processed,
        trade_segments_df=trade_segments_df,
        trade_segments_summary_df=trade_segments_summary_df,
        risk_summary_df=risk_summary_df,
        risk_events_df=risk_events_df,
        cost_df=cost_df,
        portfolio_orders_df=portfolio_orders_df,
        portfolio_fills_df=portfolio_fills_df,
        risk_orders_df=risk_orders_df,
        risk_fills_df=risk_fills_df,
        strategy_rejections_df=strategy_rejections_df,
    )


__all__ = ["merge_net_value_result_from_trading_dfs"]

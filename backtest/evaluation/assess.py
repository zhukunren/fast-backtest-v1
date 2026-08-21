"""
Assess mode calculations.
"""
from __future__ import annotations

import pandas as pd
from typing import Literal

from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.logging import log_warn

from ..context import BacktestContext
from ..netvalue.positions import add_position_and_returns_cash, add_position_and_returns_close_auto
from ..risk_control import apply_risk_control
from .trade_segment_summary import (
    build_trade_segments_from_processed_df,
    build_trade_segments_summary_df,
)


def _slot_initial_capital(ctx: BacktestContext) -> float:
    slots = int(ctx.spec.portfolio_num_j) * int(ctx.spec.portfolio_num_i)
    total = float(getattr(ctx.cfg.portfolio, "initial_capital", 1.0) or 1.0)
    if slots <= 0:
        slots = 1
    return total / slots


def build_assess_weight_map_from_daily_df(daily_df: object) -> dict[pd.Timestamp, float]:
    if not isinstance(daily_df, pd.DataFrame) or daily_df.empty:
        return {}
    if "trade_date" not in daily_df.columns:
        return {}
    if "effective_position_ratio" in daily_df.columns:
        weights = pd.to_numeric(daily_df["effective_position_ratio"], errors="coerce").fillna(1.0)
    elif "final_position_ratio" in daily_df.columns:
        weights = pd.to_numeric(daily_df["final_position_ratio"], errors="coerce").fillna(1.0)
    elif "utilization_rate" in daily_df.columns:
        weights = pd.to_numeric(daily_df["utilization_rate"], errors="coerce").fillna(1.0)
    else:
        weights = pd.Series(1.0, index=daily_df.index, dtype="float64")
    normalized_daily = ensure_trade_date_normalized(daily_df, col="trade_date")
    trade_dates = normalized_daily["trade_date"]
    weights = weights.clip(lower=0.0)
    return {d: float(w) for d, w in zip(trade_dates.tolist(), weights.tolist())}


def _build_assess_daily_df(
    ctx: BacktestContext,
    trading_df: pd.DataFrame,
    *,
    accounting_mode: Literal["close", "cash"],
) -> pd.DataFrame:
    if accounting_mode == "cash":
        return add_position_and_returns_cash(
            trading_df,
            initial_capital=_slot_initial_capital(ctx),
            portfolio_initial_capital=float(getattr(ctx.cfg.portfolio, "initial_capital", 1.0) or 1.0),
            execution_policy=ctx.execution_policy,
            cfg=ctx.cfg,
        )
    if accounting_mode == "close":
        return add_position_and_returns_close_auto(
            trading_df,
            execution_policy=ctx.execution_policy,
            cfg=ctx.cfg,
            runtime=ctx.net_value_runtime,
        )
    raise DataValidationError(f"unsupported assess accounting mode: {accounting_mode!r}")


def _apply_assess_risk_control(ctx: BacktestContext, processed_df: pd.DataFrame) -> pd.DataFrame:
    risk_cfg = ctx.risk_cfg
    if risk_cfg is None:
        raise DataValidationError("build_assess_weight_map 需要显式 risk_cfg（ctx.risk_cfg）。")
    if not bool(getattr(risk_cfg, "enabled", False)):
        return processed_df

    return apply_risk_control(
        processed_df,
        allow_intraday=bool(ctx.execution_policy.allow_intraday),
        execution_policy=ctx.execution_policy,
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
    )


def _build_assess_summary_df(
    *,
    assess_stock_count: int,
    trade_cnt: int,
    unweighted_trade_cnt: int,
    pos_cnt: int,
    neg_cnt: int,
    zero_cnt: int,
    unweighted_avg_trade_return: float,
    avg_trade_return: float,
    trade_mul: float,
    weight_sum: float,
    stock_equal_avg_trade_return: float,
) -> pd.DataFrame:
    win_rate = (float(pos_cnt) / float(trade_cnt)) if trade_cnt > 0 else 0.0
    return pd.DataFrame(
        [
            {
                "评估股票数": int(assess_stock_count),
                "交易笔数": int(trade_cnt),
                "未加权交易笔数": int(unweighted_trade_cnt),
                "收益率为正的交易笔数": int(pos_cnt),
                "收益率为负的交易笔数": int(neg_cnt),
                "收益率为0的交易笔数": int(zero_cnt),
                "胜率": float(win_rate),
                "未加权平均每笔收益率": float(unweighted_avg_trade_return),
                "按股票等权平均单笔收益率": float(stock_equal_avg_trade_return),
                "单笔平均收益率": float(avg_trade_return),
                "单笔平均收益率*交易笔数": float(trade_mul),
                "控仓加权交易权重": float(weight_sum),
            }
        ]
    )


def _merge_summary_columns(base_df: pd.DataFrame, extra_df: pd.DataFrame) -> pd.DataFrame:
    if base_df is None or base_df.empty:
        return extra_df.copy() if isinstance(extra_df, pd.DataFrame) else pd.DataFrame()
    if extra_df is None or extra_df.empty:
        return base_df

    out = base_df.copy()
    extra_row = extra_df.iloc[0]
    for col in extra_df.columns:
        out[col] = extra_row[col]
    return out


def _compute_stock_equal_avg_trade_return(
    trades_df: pd.DataFrame,
    *,
    assess_stock_count: int,
) -> float:
    if assess_stock_count <= 0 or trades_df is None or trades_df.empty:
        return 0.0
    if "stock_code" not in trades_df.columns or "盈亏" not in trades_df.columns:
        return 0.0

    code_ser = trades_df["stock_code"].astype(str).str.strip()
    valid_mask = code_ser.ne("") & code_ser.str.lower().ne("nan")
    if not bool(valid_mask.any()):
        return 0.0

    returns = pd.to_numeric(trades_df.loc[valid_mask, "盈亏"], errors="coerce").fillna(0.0)
    per_stock_avg = returns.groupby(code_ser.loc[valid_mask], sort=False).mean()
    return float(per_stock_avg.sum() / float(assess_stock_count))


def build_assess_trades_from_processed_df(processed_df: pd.DataFrame) -> pd.DataFrame:
    return build_trade_segments_from_processed_df(processed_df)


def empty_assess_summary_df(*, assess_stock_count: int) -> pd.DataFrame:
    return _build_assess_summary_df(
        assess_stock_count=assess_stock_count,
        trade_cnt=0,
        unweighted_trade_cnt=0,
        pos_cnt=0,
        neg_cnt=0,
        zero_cnt=0,
        unweighted_avg_trade_return=0.0,
        avg_trade_return=0.0,
        trade_mul=0.0,
        weight_sum=0.0,
        stock_equal_avg_trade_return=0.0,
    )


def attach_topn_trade_segments_summary(
    summary_df: pd.DataFrame,
    topn_summary_df: pd.DataFrame | None,
) -> pd.DataFrame:
    return _merge_summary_columns(summary_df, topn_summary_df if isinstance(topn_summary_df, pd.DataFrame) else pd.DataFrame())


def build_assess_summary_from_stock_results(
    stock_results: list[object] | None,
    *,
    assess_stock_count: int,
    assess_stock_codes: set[str] | None = None,
) -> pd.DataFrame:
    if not stock_results:
        return empty_assess_summary_df(assess_stock_count=assess_stock_count)

    code_filter = None
    if assess_stock_codes is not None:
        code_filter = {str(code).strip() for code in assess_stock_codes if str(code).strip()}

    rows: list[dict] = []
    for item in stock_results:
        if not isinstance(item, dict):
            continue
        code_val = item.get("stock_code") or item.get("代码")
        code = str(code_val).strip() if code_val is not None else ""
        if code_filter is not None and code not in code_filter:
            continue
        rows.append(item)

    if not rows:
        return empty_assess_summary_df(assess_stock_count=assess_stock_count)

    df = pd.DataFrame(rows)

    def _sum_col(name: str) -> float:
        if name not in df.columns:
            return 0.0
        return float(pd.to_numeric(df[name], errors="coerce").fillna(0.0).sum())

    trade_cnt = int(_sum_col("交易笔数"))
    pos_cnt = int(_sum_col("收益率为正的交易笔数"))
    neg_cnt = int(_sum_col("收益率为负的交易笔数"))
    zero_cnt = int(_sum_col("收益率为0的交易笔数"))
    if "单笔平均收益率*交易笔数" in df.columns:
        trade_mul = _sum_col("单笔平均收益率*交易笔数")
    else:
        avg_ser = (
            pd.to_numeric(df["单笔平均收益率"], errors="coerce").fillna(0.0)
            if "单笔平均收益率" in df.columns
            else pd.Series(0.0, index=df.index, dtype="float64")
        )
        cnt_ser = (
            pd.to_numeric(df["交易笔数"], errors="coerce").fillna(0.0)
            if "交易笔数" in df.columns
            else pd.Series(0.0, index=df.index, dtype="float64")
        )
        trade_mul = float((avg_ser * cnt_ser).sum())

    if "单笔平均收益率" in df.columns:
        stock_avg_ser = pd.to_numeric(df["单笔平均收益率"], errors="coerce").fillna(0.0)
    else:
        cnt_ser = (
            pd.to_numeric(df["交易笔数"], errors="coerce").fillna(0.0)
            if "交易笔数" in df.columns
            else pd.Series(0.0, index=df.index, dtype="float64")
        )
        stock_avg_ser = pd.Series(0.0, index=df.index, dtype="float64")
        positive_mask = cnt_ser > 0
        if bool(positive_mask.any()):
            stock_avg_ser.loc[positive_mask] = (
                pd.to_numeric(df.loc[positive_mask, "单笔平均收益率*交易笔数"], errors="coerce").fillna(0.0)
                / cnt_ser.loc[positive_mask]
            )

    avg_trade_return = (trade_mul / trade_cnt) if trade_cnt > 0 else 0.0
    stock_equal_avg_trade_return = (
        float(stock_avg_ser.sum() / float(assess_stock_count)) if assess_stock_count > 0 else 0.0
    )
    return _build_assess_summary_df(
        assess_stock_count=assess_stock_count,
        trade_cnt=trade_cnt,
        unweighted_trade_cnt=trade_cnt,
        pos_cnt=pos_cnt,
        neg_cnt=neg_cnt,
        zero_cnt=zero_cnt,
        unweighted_avg_trade_return=avg_trade_return,
        avg_trade_return=avg_trade_return,
        trade_mul=trade_mul,
        weight_sum=float(trade_cnt),
        stock_equal_avg_trade_return=stock_equal_avg_trade_return,
    )


def load_assess_broker_fills(
    *,
    fills_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if fills_df is None:
        raise DataValidationError("broker assess 需要显式 fills_df。")
    if not isinstance(fills_df, pd.DataFrame):
        raise DataValidationError("broker assess fills_df 必须是 DataFrame。")
    if risk_fills_df is not None and not isinstance(risk_fills_df, pd.DataFrame):
        raise DataValidationError("broker assess risk_fills_df 必须是 DataFrame。")
    base_df = fills_df.copy()
    if "source" in base_df.columns and isinstance(risk_fills_df, pd.DataFrame) and not risk_fills_df.empty:
        source_series = base_df["source"].astype(str).str.strip().str.lower()
        base_df = base_df.loc[~source_series.eq("risk")].copy()
    frames = [df for df in (base_df, risk_fills_df.copy() if isinstance(risk_fills_df, pd.DataFrame) else None) if isinstance(df, pd.DataFrame)]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0].reset_index(drop=True)
    return pd.concat(frames, ignore_index=True)


def build_assess_trades_from_broker_fills(
    fills_df: pd.DataFrame,
    *,
    risk_fills_df: pd.DataFrame | None = None,
    logger=None,
) -> pd.DataFrame:
    fills_df = load_assess_broker_fills(
        fills_df=fills_df,
        risk_fills_df=risk_fills_df,
    )
    if fills_df.empty:
        return pd.DataFrame(columns=["stock_code", "买入日", "卖出日", "盈亏"])

    required_cols = {"trade_date", "stock_code", "side", "qty", "price"}
    if not required_cols.issubset(fills_df.columns):
        missing = sorted(required_cols.difference(set(fills_df.columns)), key=str.casefold)
        raise DataValidationError(f"broker assess 缺少 fills 列: {missing}")

    norm = fills_df.copy()
    norm = ensure_trade_date_normalized(norm, col="trade_date")
    norm["stock_code"] = norm["stock_code"].astype(str).str.strip()
    norm["side"] = norm["side"].astype(str).str.strip().str.lower()
    norm["qty"] = pd.to_numeric(norm["qty"], errors="coerce").fillna(0.0)
    norm["price"] = pd.to_numeric(norm["price"], errors="coerce").fillna(0.0)
    if "total_cost" in norm.columns:
        total_cost = pd.to_numeric(norm["total_cost"], errors="coerce")
    else:
        commission = (
            pd.to_numeric(norm["commission"], errors="coerce").fillna(0.0)
            if "commission" in norm.columns
            else pd.Series(0.0, index=norm.index, dtype="float64")
        )
        stamp_tax = (
            pd.to_numeric(norm["stamp_tax"], errors="coerce").fillna(0.0)
            if "stamp_tax" in norm.columns
            else pd.Series(0.0, index=norm.index, dtype="float64")
        )
        total_cost = commission + stamp_tax
    total_cost = total_cost.fillna(0.0)
    cash_delta = (
        pd.to_numeric(norm["cash_delta"], errors="coerce")
        if "cash_delta" in norm.columns
        else pd.Series(float("nan"), index=norm.index, dtype="float64")
    )
    fallback_cash = pd.Series(0.0, index=norm.index, dtype="float64")
    buy_mask = norm["side"].eq("buy")
    sell_mask = norm["side"].eq("sell")
    notional = norm["qty"] * norm["price"]
    fallback_cash.loc[buy_mask] = -(notional.loc[buy_mask] + total_cost.loc[buy_mask])
    fallback_cash.loc[sell_mask] = notional.loc[sell_mask] - total_cost.loc[sell_mask]
    norm["fill_cash_flow"] = cash_delta.where(cash_delta.notna(), fallback_cash)
    norm = norm.loc[
        norm["stock_code"].ne("")
        & norm["side"].isin(["buy", "sell"])
        & (norm["qty"] > 0)
        & (norm["price"] > 0)
    ].copy()
    if norm.empty:
        return pd.DataFrame(columns=["stock_code", "买入日", "卖出日", "盈亏"])

    norm = norm.reset_index(drop=True)
    norm["_seq"] = pd.Series(range(len(norm)), index=norm.index, dtype="int64")
    if "order_id" in norm.columns:
        order_num = norm["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        norm["_order_num"] = pd.to_numeric(order_num, errors="coerce")
    else:
        norm["_order_num"] = pd.Series(float("nan"), index=norm.index, dtype="float64")

    trades: list[dict[str, object]] = []
    qty_eps = 1e-8
    for stock_code, grp in sorted(norm.groupby("stock_code", sort=False), key=lambda kv: str(kv[0]).casefold()):
        grp = grp.sort_values(["trade_date", "_order_num", "_seq"], kind="mergesort").reset_index(drop=True)
        open_qty = 0.0
        open_cost_cash = 0.0
        cycle_buy_cash = 0.0
        cycle_sell_cash = 0.0
        cycle_entry_date: pd.Timestamp | None = None

        for row in grp.itertuples(index=False):
            side = str(getattr(row, "side")).strip().lower()
            qty = float(getattr(row, "qty"))
            price = float(getattr(row, "price"))
            trade_date = pd.Timestamp(getattr(row, "trade_date")).normalize()
            cash_flow = float(getattr(row, "fill_cash_flow"))
            if qty <= qty_eps or price <= qty_eps:
                continue

            if side == "buy":
                buy_cash = -cash_flow
                if buy_cash <= qty_eps:
                    continue
                if open_qty <= qty_eps:
                    cycle_entry_date = trade_date
                    cycle_buy_cash = 0.0
                    cycle_sell_cash = 0.0
                open_qty += qty
                open_cost_cash += buy_cash
                cycle_buy_cash += buy_cash
                continue

            if side != "sell" or open_qty <= qty_eps:
                continue
            sell_qty = qty if qty <= open_qty else open_qty
            if sell_qty <= qty_eps:
                continue
            sell_cash = cash_flow
            if sell_cash <= qty_eps:
                continue
            avg_cost_cash = (open_cost_cash / open_qty) if open_qty > qty_eps else 0.0
            close_buy_cash = avg_cost_cash * sell_qty
            close_sell_cash = sell_cash * (sell_qty / qty)

            open_qty -= sell_qty
            open_cost_cash -= close_buy_cash
            cycle_sell_cash += close_sell_cash
            if open_cost_cash < qty_eps:
                open_cost_cash = 0.0
            if open_qty <= qty_eps:
                open_qty = 0.0
                if cycle_entry_date is not None and cycle_buy_cash > qty_eps:
                    trade_return = cycle_sell_cash / cycle_buy_cash - 1.0
                    trades.append(
                        {
                            "stock_code": str(stock_code),
                            "买入日": cycle_entry_date,
                            "卖出日": trade_date,
                            "盈亏": float(trade_return),
                        }
                    )
                cycle_entry_date = None
                cycle_buy_cash = 0.0
                cycle_sell_cash = 0.0

        if open_qty > qty_eps:
            last_trade_date = pd.Timestamp(grp["trade_date"].iloc[-1]).normalize()
            if cycle_entry_date is not None and cycle_buy_cash > qty_eps:
                forced_sell_cash = cycle_sell_cash + open_cost_cash
                trade_return = forced_sell_cash / cycle_buy_cash - 1.0
                trades.append(
                    {
                        "stock_code": str(stock_code),
                        "买入日": cycle_entry_date,
                        "卖出日": last_trade_date,
                        "盈亏": float(trade_return),
                    }
                )
            log_warn(
                f"assess broker: 存在未平仓成交，按成本价估算强平 stock_code={stock_code} qty={open_qty:.4f}",
                logger,
            )

    if not trades:
        return pd.DataFrame(columns=["stock_code", "买入日", "卖出日", "盈亏"])
    out = pd.DataFrame(trades)
    out = ensure_trade_date_normalized(out, col="买入日")
    out = ensure_trade_date_normalized(out, col="卖出日")
    out["盈亏"] = pd.to_numeric(out["盈亏"], errors="coerce").fillna(0.0)
    return out.sort_values(["买入日", "stock_code", "卖出日"], kind="mergesort").reset_index(drop=True)


def build_assess_summary_from_broker(
    ctx: BacktestContext,
    *,
    merged_df: pd.DataFrame | None,
    assess_stock_count: int,
    fills_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if fills_df is None:
        raise DataValidationError("broker assess 需要显式 fills_df。")
    trades_df = build_assess_trades_from_broker_fills(
        fills_df,
        risk_fills_df=risk_fills_df,
        logger=ctx.logger,
    )
    if trades_df.empty:
        return empty_assess_summary_df(assess_stock_count=assess_stock_count)

    eps = 1e-8
    returns = pd.to_numeric(trades_df["盈亏"], errors="coerce").fillna(0.0)
    unweighted_trade_cnt = int(len(returns))
    unweighted_sum = float(returns.sum())
    stock_equal_avg_trade_return = _compute_stock_equal_avg_trade_return(
        trades_df,
        assess_stock_count=assess_stock_count,
    )

    risk_cfg = ctx.risk_cfg
    if risk_cfg is None:
        raise DataValidationError("build_assess_summary_from_broker 需要显式 risk_cfg（ctx.risk_cfg）。")
    weight_df = merged_df

    def _rebuild_weight_from_merged(df_in: pd.DataFrame) -> pd.DataFrame:
        if df_in is None or df_in.empty:
            return df_in
        enable_nv = bool(getattr(risk_cfg, "enable_nv", True))
        enable_dd = bool(getattr(risk_cfg, "enable_dd", True))
        if not (enable_nv or enable_dd):
            return df_in

        if "net_value" in df_in.columns:
            net_value = pd.to_numeric(df_in["net_value"], errors="coerce")
        elif "sum_net_value" in df_in.columns:
            net_value = pd.to_numeric(df_in["sum_net_value"], errors="coerce")
        elif "equity" in df_in.columns:
            init_capital = float(getattr(ctx.cfg.portfolio, "initial_capital", 1.0) or 1.0)
            net_value = pd.to_numeric(df_in["equity"], errors="coerce") / init_capital
        else:
            net_value = pd.Series(1.0, index=df_in.index, dtype="float64")

        net_value = net_value.ffill().fillna(1.0)
        peak = net_value.cummax().where(lambda x: x > 0, 1.0)
        drawdown = (net_value / peak - 1.0).fillna(0.0)

        nv_ratios = tuple(float(x) for x in tuple(getattr(risk_cfg, "nv_ratios", (1.0, 0.8, 0.5, 0.3))))
        dd_ratios = tuple(float(x) for x in tuple(getattr(risk_cfg, "dd_ratios", (1.0, 0.8, 0.5, 0.3))))
        nv_warn = float(getattr(risk_cfg, "nv_warn", 0.9))
        nv_danger = float(getattr(risk_cfg, "nv_danger", 0.8))
        nv_clear = float(getattr(risk_cfg, "nv_clear", 0.5))
        warn_dd = float(getattr(risk_cfg, "warn_dd", -0.10))
        danger_dd = float(getattr(risk_cfg, "danger_dd", -0.20))
        lock_dd = float(getattr(risk_cfg, "lock_dd", -0.30))

        final_ratios: list[float] = []
        for nv, dd in zip(net_value.tolist(), drawdown.tolist()):
            if enable_nv:
                if float(nv) < nv_clear:
                    nv_ratio = nv_ratios[3]
                elif float(nv) < nv_danger:
                    nv_ratio = nv_ratios[2]
                elif float(nv) < nv_warn:
                    nv_ratio = nv_ratios[1]
                else:
                    nv_ratio = nv_ratios[0]
            else:
                nv_ratio = 1.0

            if enable_dd:
                if float(dd) <= lock_dd:
                    dd_ratio = dd_ratios[3]
                elif float(dd) <= danger_dd:
                    dd_ratio = dd_ratios[2]
                elif float(dd) <= warn_dd:
                    dd_ratio = dd_ratios[1]
                else:
                    dd_ratio = dd_ratios[0]
            else:
                dd_ratio = 1.0

            final_ratios.append(float(min(nv_ratio, dd_ratio)))

        out_df = df_in.copy()
        out_df["final_position_ratio"] = pd.Series(final_ratios, index=out_df.index, dtype="float64")
        return out_df

    if (
        isinstance(weight_df, pd.DataFrame)
        and (not weight_df.empty)
        and bool(getattr(risk_cfg, "enabled", False))
    ):
        # broker 路径优先使用运行期已产出的 final_position_ratio；仅在缺失时回退重建
        if "final_position_ratio" not in weight_df.columns:
            weight_df = _rebuild_weight_from_merged(weight_df)

    weight_map = build_assess_weight_map_from_daily_df(weight_df)
    buy_dates = pd.to_datetime(trades_df["买入日"], errors="coerce").dt.normalize()
    weights = pd.to_numeric(buy_dates.map(weight_map), errors="coerce").fillna(1.0).clip(lower=0.0)
    effective = weights > eps

    trade_cnt = 0
    pos_cnt = 0
    neg_cnt = 0
    zero_cnt = 0
    weighted_sum = 0.0
    weight_sum = 0.0
    if bool(effective.any()):
        eff_returns = returns.loc[effective]
        eff_weights = weights.loc[effective]
        weighted_returns = eff_returns * eff_weights
        trade_cnt = int(len(eff_returns))
        pos_cnt = int((weighted_returns > eps).sum())
        neg_cnt = int((weighted_returns < -eps).sum())
        zero_cnt = int((weighted_returns.abs() <= eps).sum())
        weighted_sum = float(weighted_returns.sum())
        weight_sum = float(eff_weights.sum())

    unweighted_avg_trade_return = (unweighted_sum / unweighted_trade_cnt) if unweighted_trade_cnt > 0 else 0.0
    avg_trade_return = (weighted_sum / weight_sum) if weight_sum > 0 else 0.0
    trade_mul = avg_trade_return * trade_cnt
    return _build_assess_summary_df(
        assess_stock_count=assess_stock_count,
        trade_cnt=trade_cnt,
        unweighted_trade_cnt=unweighted_trade_cnt,
        pos_cnt=pos_cnt,
        neg_cnt=neg_cnt,
        zero_cnt=zero_cnt,
        unweighted_avg_trade_return=unweighted_avg_trade_return,
        avg_trade_return=avg_trade_return,
        trade_mul=trade_mul,
        weight_sum=weight_sum,
        stock_equal_avg_trade_return=stock_equal_avg_trade_return,
    )

def build_actual_trade_segments_from_broker(
    *,
    fills_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
    logger=None,
) -> pd.DataFrame:
    if fills_df is None:
        raise DataValidationError("broker trade segments require fills_df")
    return build_assess_trades_from_broker_fills(
        fills_df,
        risk_fills_df=risk_fills_df,
        logger=logger,
    )


def build_actual_trade_segments_summary_from_broker(
    *,
    prefix: str = "实际交易段",
    fills_df: pd.DataFrame | None = None,
    risk_fills_df: pd.DataFrame | None = None,
    logger=None,
) -> pd.DataFrame:
    trades_df = build_actual_trade_segments_from_broker(
        fills_df=fills_df,
        risk_fills_df=risk_fills_df,
        logger=logger,
    )
    return build_trade_segments_summary_df(trades_df, prefix=prefix)


def build_assess_summary_from_segments(
    ctx: BacktestContext,
    trading_dfs: dict[str, pd.DataFrame],
    *,
    assess_stock_count: int,
    accounting_mode: Literal["close", "cash"],
) -> pd.DataFrame:
    if not trading_dfs:
        return empty_assess_summary_df(assess_stock_count=assess_stock_count)

    eps = 1e-8
    trade_cnt = 0
    pos_cnt = 0
    neg_cnt = 0
    zero_cnt = 0
    weighted_sum = 0.0
    weight_sum = 0.0
    unweighted_trade_cnt = 0
    unweighted_sum = 0.0
    all_trades: list[pd.DataFrame] = []

    for name, trading_df in sorted(trading_dfs.items(), key=lambda kv: str(kv[0]).casefold()):
        if trading_df is None or trading_df.empty:
            continue
        processed_df = _build_assess_daily_df(
            ctx,
            trading_df,
            accounting_mode=accounting_mode,
        )
        trades_df = build_assess_trades_from_processed_df(processed_df)
        if trades_df is None or trades_df.empty:
            continue

        all_trades.append(trades_df.copy())
        returns = pd.to_numeric(trades_df["盈亏"], errors="coerce").fillna(0.0)
        unweighted_trade_cnt += int(len(returns))
        unweighted_sum += float(returns.sum())
        weight_df = _apply_assess_risk_control(ctx, processed_df.copy())
        weight_map = build_assess_weight_map_from_daily_df(weight_df)
        buy_dates = pd.to_datetime(trades_df["买入日"], errors="coerce").dt.normalize()
        weights = pd.to_numeric(buy_dates.map(weight_map), errors="coerce").fillna(1.0).clip(lower=0.0)

        effective = weights > eps
        if not bool(effective.any()):
            continue

        eff_returns = returns.loc[effective]
        eff_weights = weights.loc[effective]
        weighted_returns = eff_returns * eff_weights
        trade_cnt += int(len(eff_returns))
        pos_cnt += int((weighted_returns > eps).sum())
        neg_cnt += int((weighted_returns < -eps).sum())
        zero_cnt += int((weighted_returns.abs() <= eps).sum())
        weighted_sum += float(weighted_returns.sum())
        weight_sum += float(eff_weights.sum())

    unweighted_avg_trade_return = (unweighted_sum / unweighted_trade_cnt) if unweighted_trade_cnt > 0 else 0.0
    avg_trade_return = (weighted_sum / weight_sum) if weight_sum > 0 else 0.0
    trade_mul = avg_trade_return * trade_cnt
    all_trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    stock_equal_avg_trade_return = _compute_stock_equal_avg_trade_return(
        all_trades_df,
        assess_stock_count=assess_stock_count,
    )
    return _build_assess_summary_df(
        assess_stock_count=assess_stock_count,
        trade_cnt=trade_cnt,
        unweighted_trade_cnt=unweighted_trade_cnt,
        pos_cnt=pos_cnt,
        neg_cnt=neg_cnt,
        zero_cnt=zero_cnt,
        unweighted_avg_trade_return=unweighted_avg_trade_return,
        avg_trade_return=avg_trade_return,
        trade_mul=trade_mul,
        weight_sum=weight_sum,
        stock_equal_avg_trade_return=stock_equal_avg_trade_return,
    )

from __future__ import annotations

from enum import Enum, auto
from typing import Literal

import numpy as np
import pandas as pd
from core.constants import (
    COL_TRADE_DATE,
    DIRECTION_BUY,
    DIRECTION_SELL,
    RATIO_TOLERANCE,
    SIGNAL_COL_DIRECTION,
)
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized
from core.normalizers import normalize_direction_series

from .execution_semantics import resolve_execution_semantics, resolve_trade_flags


class RiskState(Enum):
    """净值风控 FSM 状态。"""

    NORMAL = auto()
    REDUCE_80 = auto()
    REDUCE_50 = auto()
    CLEAR = auto()


class DrawdownState(Enum):
    """回撤风控 FSM 状态。"""

    NORMAL = auto()
    WARNING = auto()
    DANGER = auto()
    LOCK = auto()

_NV_RATIO_DEFAULT = (1.0, 0.8, 0.5, 0.3)
_DD_RATIO_DEFAULT = (1.0, 0.8, 0.5, 0.3)
_NV_THRESH_DEFAULT = (0.9, 0.8, 0.5)

DailyReturnScope = Literal["invested", "portfolio"]
_PORTFOLIO_RATIO_INPUT_COL = "__risk_portfolio_position_ratio_input__"


def _nv_state_from_equity(
    equity: float,
    warn_nv: float,
    danger_nv: float,
    clear_nv: float,
) -> RiskState:
    if equity < clear_nv:
        return RiskState.CLEAR
    if equity < danger_nv:
        return RiskState.REDUCE_50
    if equity < warn_nv:
        return RiskState.REDUCE_80
    return RiskState.NORMAL


def _nv_ratio_from_state(state: RiskState, ratios: tuple[float, float, float, float]) -> float:
    normal, reduce_80, reduce_50, clear = ratios
    if state == RiskState.NORMAL:
        return normal
    if state == RiskState.REDUCE_80:
        return reduce_80
    if state == RiskState.REDUCE_50:
        return reduce_50
    return clear


def _dd_state_from_drawdown(
    dd: float,
    warn_dd: float,
    danger_dd: float,
    lock_dd: float,
) -> DrawdownState:
    if dd <= lock_dd:
        return DrawdownState.LOCK
    if dd <= danger_dd:
        return DrawdownState.DANGER
    if dd <= warn_dd:
        return DrawdownState.WARNING
    return DrawdownState.NORMAL


def _dd_ratio_from_state(state: DrawdownState, ratios: tuple[float, float, float, float]) -> float:
    normal, warning, danger, lock = ratios
    if state == DrawdownState.NORMAL:
        return normal
    if state == DrawdownState.WARNING:
        return warning
    if state == DrawdownState.DANGER:
        return danger
    return lock


def _build_position(entry: np.ndarray, exit_: np.ndarray, *, allow_intraday: bool = True) -> np.ndarray:
    pos = np.zeros(len(entry), dtype=int)
    prev = 0
    for i in range(len(entry)):
        if prev == 0 and entry[i]:
            if exit_[i] and (not allow_intraday):
                pos[i] = 1
            else:
                pos[i] = 0 if exit_[i] else 1
        elif prev == 1 and exit_[i]:
            pos[i] = 0
        else:
            pos[i] = prev
        prev = pos[i]
    return pos


def _compute_daily_return(
    open_s: pd.Series,
    close_s: pd.Series,
    entry: np.ndarray,
    exit_: np.ndarray,
    position: np.ndarray,
    *,
    allow_intraday: bool = True,
    buy_timing: str | None = None,
    sell_timing: str | None = None,
) -> np.ndarray:
    open_arr = pd.to_numeric(open_s, errors="coerce").to_numpy(dtype="float64", copy=False)
    close_arr = pd.to_numeric(close_s, errors="coerce").to_numpy(dtype="float64", copy=False)
    n = len(open_arr)
    prev_close = np.roll(close_arr, 1)
    if n > 0:
        prev_close[0] = 0.0

    oc_return = np.zeros(n, dtype="float64")
    oc_mask = open_arr != 0
    if oc_mask.any():
        oc_return[oc_mask] = close_arr[oc_mask] / open_arr[oc_mask] - 1.0

    cc_return = np.zeros(n, dtype="float64")
    cc_mask = prev_close != 0
    if cc_mask.any():
        cc_return[cc_mask] = close_arr[cc_mask] / prev_close[cc_mask] - 1.0
    if n > 0:
        cc_return[0] = 0.0

    prev_pos = np.roll(position, 1)
    if n > 0:
        prev_pos[0] = 0

    buy_timing = str(buy_timing or "").strip().lower() or "next_open"
    if allow_intraday:
        mask_intraday = (prev_pos == 0) & entry & exit_
        mask_open_only = (prev_pos == 0) & entry & (~exit_)
    else:
        mask_intraday = np.zeros(n, dtype=bool)
        mask_open_only = (prev_pos == 0) & entry
    mask_hold = (prev_pos == 1) & (~exit_)
    mask_close = (prev_pos == 1) & exit_

    daily = np.zeros(n, dtype="float64")
    if buy_timing == "signal_close":
        daily[mask_open_only | mask_intraday] = 0.0
    else:
        daily[mask_open_only | mask_intraday] = oc_return[mask_open_only | mask_intraday]
    if str(sell_timing or "").lower() == "next_open":
        daily[mask_hold] = cc_return[mask_hold]
        oc_prev = np.zeros(n, dtype="float64")
        if cc_mask.any():
            oc_prev[cc_mask] = open_arr[cc_mask] / prev_close[cc_mask] - 1.0
        daily[mask_close] = oc_prev[mask_close]
    else:
        daily[mask_hold | mask_close] = cc_return[mask_hold | mask_close]
    return daily


def _rebuild_daily_return(
    df: pd.DataFrame,
    *,
    entry: np.ndarray,
    exit_: np.ndarray,
    position: np.ndarray,
    allow_intraday: bool,
    buy_timing: str | None,
    sell_timing: str | None,
) -> np.ndarray:
    open_s = pd.to_numeric(df["open"], errors="raise")
    close_s = pd.to_numeric(df["close"], errors="raise")
    daily_returns = _compute_daily_return(
        open_s,
        close_s,
        entry,
        exit_,
        position,
        allow_intraday=allow_intraday,
        buy_timing=buy_timing,
        sell_timing=sell_timing,
    )
    if "cost_rate" not in df.columns:
        return daily_returns
    cost_rate = pd.to_numeric(df["cost_rate"], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    return (1.0 + daily_returns) * (1.0 - cost_rate) - 1.0


def _resolve_entry_exit_for_policy(
    direction: pd.Series,
    *,
    execution_policy,
    allow_intraday: bool,
) -> tuple[np.ndarray, np.ndarray, bool, str, str]:
    if execution_policy is not None:
        semantics = resolve_execution_semantics(execution_policy)
        resolved = resolve_trade_flags(
            execution_policy=execution_policy,
            direction_series=direction,
            stock_code=None,
            aggregate_t1_stats=False,
            cfg=None,
        )
        return (
            resolved.entry_flags,
            resolved.exit_flags,
            bool(semantics.allow_intraday),
            semantics.buy_timing,
            semantics.sell_timing,
        )

    entry = direction.shift(1).eq(DIRECTION_BUY).to_numpy(dtype=bool)
    exit_ = direction.eq(DIRECTION_SELL).to_numpy(dtype=bool)
    return entry, exit_, bool(allow_intraday), "next_open", "signal_close"


def _infer_full_exec_flags_from_trade_prices(
    out: pd.DataFrame,
    *,
    position: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    if "buy_price" not in out.columns or "sell_price" not in out.columns:
        return None
    buy_exec = pd.to_numeric(out["buy_price"], errors="coerce").notna().to_numpy(dtype=bool)
    sell_exec = pd.to_numeric(out["sell_price"], errors="coerce").notna().to_numpy(dtype=bool)
    if len(position) == len(sell_exec):
        # Risk FSM models full position exits; partial fills keep invested equity alive.
        sell_exec = sell_exec & (np.asarray(position, dtype=int) == 0)
    return buy_exec, sell_exec


def _normalize_portfolio_position_ratio(
    daily_returns: np.ndarray,
    *,
    daily_return_scope: DailyReturnScope,
    portfolio_position_ratio: np.ndarray | None,
) -> np.ndarray | None:
    if daily_return_scope not in {"invested", "portfolio"}:
        raise DataValidationError(
            f"daily_return_scope={daily_return_scope!r} 非法，仅支持 invested/portfolio。"
        )
    if daily_return_scope == "invested":
        return None
    if portfolio_position_ratio is None:
        raise DataValidationError(
            "daily_return_scope='portfolio' 时必须提供 portfolio_position_ratio。"
        )
    ratio = np.asarray(portfolio_position_ratio, dtype="float64")
    if len(ratio) != len(daily_returns):
        raise DataValidationError("portfolio_position_ratio 长度必须与 daily_returns 一致。")
    if not np.isfinite(ratio).all():
        raise DataValidationError("portfolio_position_ratio 必须全部为有限数值。")
    return np.clip(ratio, 0.0, None)


def _mark_risk_equity_for_day(
    *,
    daily_return: float,
    bought: bool,
    position_active: bool,
    current_ratio: float,
    raw_portfolio_ratio: float | None,
    equity: float,
    cash: float,
    invested: float,
) -> tuple[float, float, float]:
    if raw_portfolio_ratio is not None:
        effective_ratio = min(raw_portfolio_ratio, current_ratio) if position_active else 0.0
        exposure_scale = (
            effective_ratio / raw_portfolio_ratio
            if raw_portfolio_ratio > RATIO_TOLERANCE
            else 1.0
        )
        equity *= 1 + daily_return * exposure_scale
        if not position_active:
            return equity, equity, 0.0
        cash = equity * (1 - effective_ratio)
        return equity, cash, equity - cash

    if bought:
        cash = equity * (1 - current_ratio)
        invested = equity - cash
    invested *= 1 + daily_return
    return cash + invested, cash, invested


def _allocate_risk_equity(
    *,
    equity: float,
    position_active: bool,
    current_ratio: float,
    raw_portfolio_ratio: float | None,
) -> tuple[float, float]:
    if not position_active:
        return equity, 0.0
    allocation_ratio = current_ratio
    if raw_portfolio_ratio is not None:
        allocation_ratio = min(raw_portfolio_ratio, current_ratio)
    cash = equity * (1 - allocation_ratio)
    return cash, equity - cash


def apply_risk_fsm(
    daily_returns: np.ndarray,
    buy_exec: np.ndarray,
    sell_exec: np.ndarray,
    position: np.ndarray,
    *,
    enable_nv: bool = True,
    enable_dd: bool = True,
    warn_dd: float,
    danger_dd: float,
    lock_dd: float,
    nv_warn: float | None = None,
    nv_danger: float | None = None,
    nv_clear: float | None = None,
    nv_ratios: tuple[float, float, float, float] | None = None,
    dd_ratios: tuple[float, float, float, float] | None = None,
    daily_return_scope: DailyReturnScope = "invested",
    portfolio_position_ratio: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """基于净值/回撤阈值的风控 FSM（收盘更新，收盘换仓）。

    ``invested`` 表示 ``daily_returns`` 是风险资产本身的收益，收益只作用于
    ``invested``；这是单股路径的默认口径。``portfolio`` 表示收益已经包含现金
    仓位，必须同时传入原组合的 ``portfolio_position_ratio``。此时仅在风控上限
    低于原组合仓位时按相对敞口缩放，避免把组合收益再次乘以原仓位率。
    """
    if nv_ratios is None:
        nv_ratios = _NV_RATIO_DEFAULT
    if dd_ratios is None:
        dd_ratios = _DD_RATIO_DEFAULT
    if nv_warn is None:
        nv_warn = _NV_THRESH_DEFAULT[0]
    if nv_danger is None:
        nv_danger = _NV_THRESH_DEFAULT[1]
    if nv_clear is None:
        nv_clear = _NV_THRESH_DEFAULT[2]
    raw_portfolio_ratio = _normalize_portfolio_position_ratio(
        daily_returns,
        daily_return_scope=daily_return_scope,
        portfolio_position_ratio=portfolio_position_ratio,
    )

    nv_state = RiskState.NORMAL
    dd_state = DrawdownState.NORMAL
    nv_ratio = 1.0
    dd_ratio = 1.0

    equity = 1.0
    peak = 1.0
    cash = equity
    invested = 0.0
    current_ratio = 1.0
    pending_reduce = False
    ratio_tol = RATIO_TOLERANCE

    n = len(daily_returns)
    risk_state = np.empty(n, dtype=object)
    nv_position_ratio = np.zeros(n, dtype="float64")
    dd_state_list = np.empty(n, dtype=object)
    dd_position_ratio = np.zeros(n, dtype="float64")
    final_position_ratio = np.zeros(n, dtype="float64")
    equity_final = np.zeros(n, dtype="float64")
    cash_equity = np.zeros(n, dtype="float64")
    invested_equity = np.zeros(n, dtype="float64")
    drawdown = np.zeros(n, dtype="float64")

    for i in range(n):
        raw_ratio = float(raw_portfolio_ratio[i]) if raw_portfolio_ratio is not None else None
        equity, cash, invested = _mark_risk_equity_for_day(
            daily_return=float(daily_returns[i]),
            bought=bool(buy_exec[i]),
            position_active=bool(position[i] == 1),
            current_ratio=current_ratio,
            raw_portfolio_ratio=raw_ratio,
            equity=equity,
            cash=cash,
            invested=invested,
        )

        if sell_exec[i]:
            cash = equity
            invested = 0.0

        if equity > peak:
            peak = equity
        dd = equity / peak - 1.0
        drawdown[i] = dd

        if enable_nv:
            nv_state = _nv_state_from_equity(equity, nv_warn, nv_danger, nv_clear)
            nv_ratio = _nv_ratio_from_state(nv_state, nv_ratios)
        else:
            nv_state = RiskState.NORMAL
            nv_ratio = 1.0
        if enable_dd:
            dd_state = _dd_state_from_drawdown(dd, warn_dd, danger_dd, lock_dd)
            dd_ratio = _dd_ratio_from_state(dd_state, dd_ratios)
        else:
            dd_state = DrawdownState.NORMAL
            dd_ratio = 1.0

        next_ratio = nv_ratio if nv_ratio < dd_ratio else dd_ratio

        if pending_reduce:
            if next_ratio < current_ratio:
                current_ratio = next_ratio
                cash, invested = _allocate_risk_equity(
                    equity=equity,
                    position_active=bool(position[i] == 1),
                    current_ratio=current_ratio,
                    raw_portfolio_ratio=raw_ratio,
                )
            pending_reduce = False

        if abs(next_ratio - current_ratio) > ratio_tol:
            # 买入执行日触发减仓，延迟到下一交易日收盘复核（信号日视角为 T+2）
            if next_ratio < current_ratio and buy_exec[i] and position[i] == 1:
                pending_reduce = True
            else:
                current_ratio = next_ratio
                cash, invested = _allocate_risk_equity(
                    equity=equity,
                    position_active=bool(position[i] == 1),
                    current_ratio=current_ratio,
                    raw_portfolio_ratio=raw_ratio,
                )

        risk_state[i] = nv_state.name
        nv_position_ratio[i] = nv_ratio
        dd_state_list[i] = dd_state.name
        dd_position_ratio[i] = dd_ratio
        final_position_ratio[i] = current_ratio
        equity_final[i] = equity
        cash_equity[i] = cash
        invested_equity[i] = invested

    return {
        "risk_state": risk_state,
        "nv_position_ratio": nv_position_ratio,
        "drawdown": drawdown,
        "dd_state": dd_state_list,
        "dd_position_ratio": dd_position_ratio,
        "final_position_ratio": final_position_ratio,
        "equity_final": equity_final,
        "cash_equity": cash_equity,
        "invested_equity": invested_equity,
    }


def _resolve_effective_position_ratio(
    *,
    raw_ratio: pd.Series | np.ndarray | None,
    final_ratio: pd.Series | np.ndarray,
) -> np.ndarray:
    final_ser = pd.Series(final_ratio, dtype="float64")
    final_arr = pd.to_numeric(final_ser, errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype="float64")
    if raw_ratio is None:
        return final_arr
    raw_ser = pd.Series(raw_ratio, dtype="float64")
    raw_arr = pd.to_numeric(raw_ser, errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype="float64")
    return np.minimum(raw_arr, final_arr)


def _resolve_portfolio_ratio_input(
    out: pd.DataFrame,
    *,
    daily_return_scope: DailyReturnScope,
    portfolio_position_ratio: pd.Series | np.ndarray | None,
) -> np.ndarray | None:
    resolved = portfolio_position_ratio
    if resolved is None:
        if daily_return_scope != "portfolio":
            return None
        if "utilization_rate" not in out.columns:
            raise DataValidationError(
                "portfolio 收益口径的 risk_control 缺少 utilization_rate。"
            )
        resolved = pd.to_numeric(
            out["utilization_rate"],
            errors="raise",
        ).to_numpy(dtype="float64", copy=False)
    return np.asarray(resolved, dtype="float64")


def _attach_portfolio_ratio_input(
    df: pd.DataFrame,
    portfolio_position_ratio: pd.Series | np.ndarray | None,
) -> pd.DataFrame:
    if portfolio_position_ratio is None:
        return df
    ratio_input = np.asarray(portfolio_position_ratio, dtype="float64")
    if len(ratio_input) != len(df):
        raise DataValidationError("portfolio_position_ratio 长度必须与输入 DataFrame 一致。")
    if _PORTFOLIO_RATIO_INPUT_COL in df.columns:
        raise DataValidationError(f"输入 DataFrame 不得包含保留列 {_PORTFOLIO_RATIO_INPUT_COL}。")
    df[_PORTFOLIO_RATIO_INPUT_COL] = ratio_input
    return df


def _pop_sorted_portfolio_ratio_input(out: pd.DataFrame) -> np.ndarray | None:
    if _PORTFOLIO_RATIO_INPUT_COL not in out.columns:
        return None
    return out.pop(_PORTFOLIO_RATIO_INPUT_COL).to_numpy(dtype="float64", copy=False)


def apply_risk_control(
    df: pd.DataFrame,
    *,
    enable_nv: bool = True,
    enable_dd: bool = True,
    warn_dd: float = -0.10,
    danger_dd: float = -0.20,
    lock_dd: float = -0.30,
    nv_warn: float = 0.9,
    nv_danger: float = 0.8,
    nv_clear: float = 0.5,
    nv_ratios: tuple[float, float, float, float] | None = None,
    dd_ratios: tuple[float, float, float, float] | None = None,
    keep_raw_net_value: bool = True,
    allow_intraday: bool | None = None,
    execution_policy=None,
    daily_return_scope: DailyReturnScope = "invested",
    portfolio_position_ratio: pd.Series | np.ndarray | None = None,
) -> pd.DataFrame:
    """
    对净值序列应用风控，输出 risk_* 相关字段，并用风控后净值覆盖 net_value。
    """
    out = _attach_portfolio_ratio_input(df.copy(), portfolio_position_ratio)

    if COL_TRADE_DATE in out.columns:
        out = ensure_trade_date_normalized(out, col=COL_TRADE_DATE)
        if not out[COL_TRADE_DATE].is_monotonic_increasing:
            out = out.sort_values(COL_TRADE_DATE, kind="mergesort").reset_index(drop=True)

    for col in ("open", "close", SIGNAL_COL_DIRECTION):
        if col not in out.columns:
            raise DataValidationError(f"risk_control 缺少列: {col}")

    if allow_intraday is None:
        allow_intraday = False
    direction = normalize_direction_series(out[SIGNAL_COL_DIRECTION])
    entry, exit_, allow_intraday, buy_timing, sell_timing = _resolve_entry_exit_for_policy(
        direction,
        execution_policy=execution_policy,
        allow_intraday=bool(allow_intraday),
    )

    if "position" in out.columns:
        position = pd.to_numeric(out["position"], errors="raise").fillna(0).astype(int).to_numpy()
    else:
        position = _build_position(entry, exit_, allow_intraday=allow_intraday)
        out["position"] = position

    inferred_exec = _infer_full_exec_flags_from_trade_prices(out, position=position)
    if inferred_exec is not None:
        buy_exec, sell_exec = inferred_exec
    else:
        prev_pos = np.roll(position, 1)
        if len(prev_pos) > 0:
            prev_pos[0] = 0
        buy_exec = (prev_pos == 0) & entry
        if allow_intraday:
            sell_exec = ((prev_pos == 1) & exit_) | ((prev_pos == 0) & entry & exit_)
        else:
            sell_exec = (prev_pos == 1) & exit_

    rebuilt_daily_returns = None
    if "daily_return" in out.columns:
        daily_returns = pd.to_numeric(out["daily_return"], errors="coerce").to_numpy(dtype="float64", copy=True)
        missing_mask = np.isnan(daily_returns)
        if missing_mask.any():
            rebuilt_daily_returns = _rebuild_daily_return(
                out,
                entry=entry,
                exit_=exit_,
                position=position,
                allow_intraday=allow_intraday,
                buy_timing=buy_timing,
                sell_timing=sell_timing,
            )
            daily_returns = daily_returns.copy()
            daily_returns[missing_mask] = rebuilt_daily_returns[missing_mask]
    else:
        daily_returns = _rebuild_daily_return(
            out,
            entry=entry,
            exit_=exit_,
            position=position,
            allow_intraday=allow_intraday,
            buy_timing=buy_timing,
            sell_timing=sell_timing,
        )
    if not np.isfinite(daily_returns).all():
        bad_cols = [COL_TRADE_DATE, "open", "close", SIGNAL_COL_DIRECTION, "position", "daily_return", "cost_rate"]
        invalid_mask = ~np.isfinite(daily_returns)
        sample = out.loc[invalid_mask, [c for c in bad_cols if c in out.columns]].head(5).to_dict(orient="records")
        raise DataValidationError(f"daily_return 存在非有限数值，且无法根据价格重建（示例：{sample}）。")
    out["daily_return"] = daily_returns

    resolved_portfolio_ratio = _resolve_portfolio_ratio_input(
        out,
        daily_return_scope=daily_return_scope,
        portfolio_position_ratio=_pop_sorted_portfolio_ratio_input(out),
    )
    risk_cols = apply_risk_fsm(
        daily_returns,
        buy_exec,
        sell_exec,
        position,
        enable_nv=enable_nv,
        enable_dd=enable_dd,
        warn_dd=warn_dd,
        danger_dd=danger_dd,
        lock_dd=lock_dd,
        nv_warn=nv_warn,
        nv_danger=nv_danger,
        nv_clear=nv_clear,
        nv_ratios=nv_ratios,
        dd_ratios=dd_ratios,
        daily_return_scope=daily_return_scope,
        portfolio_position_ratio=resolved_portfolio_ratio,
    )
    for key, val in risk_cols.items():
        out[key] = val

    raw_ratio = None
    if "utilization_rate" in out.columns:
        raw_ratio = pd.to_numeric(out["utilization_rate"], errors="coerce").fillna(0.0)
        if "utilization_rate_raw" not in out.columns:
            out["utilization_rate_raw"] = raw_ratio
    elif "position" in out.columns:
        raw_ratio = pd.to_numeric(out["position"], errors="coerce").fillna(0.0)

    out["effective_position_ratio"] = _resolve_effective_position_ratio(
        raw_ratio=raw_ratio,
        final_ratio=out["final_position_ratio"],
    )
    if keep_raw_net_value and "net_value" in out.columns and "net_value_raw" not in out.columns:
        out["net_value_raw"] = out["net_value"]
    out["net_value"] = out["equity_final"]
    out["utilization_rate"] = out["effective_position_ratio"]
    return out

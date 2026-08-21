"""
Trade-segment summary helpers built from processed return data.
"""
from __future__ import annotations

import pandas as pd

from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized


_TRADE_COLS = ["stock_code", "买入日", "卖出日", "盈亏"]


def empty_trade_segments_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_TRADE_COLS)


def empty_trade_segments_summary_df(*, prefix: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                f"{prefix}交易笔数": 0,
                f"{prefix}收益率为正的交易笔数": 0,
                f"{prefix}收益率为负的交易笔数": 0,
                f"{prefix}收益率为0的交易笔数": 0,
                f"{prefix}平均每笔收益率": 0.0,
                f"{prefix}胜率": 0.0,
            }
        ]
    )


def build_trade_segments_from_processed_df(processed_df: pd.DataFrame) -> pd.DataFrame:
    if processed_df is None or processed_df.empty:
        return empty_trade_segments_df()
    if "trade_date" not in processed_df.columns:
        raise DataValidationError("build_trade_segments_from_processed_df 缺少 trade_date 列。")

    norm = processed_df.copy()
    norm = ensure_trade_date_normalized(norm, col="trade_date")
    if not norm["trade_date"].is_monotonic_increasing:
        norm = norm.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
    elif not isinstance(norm.index, pd.RangeIndex):
        norm = norm.reset_index(drop=True)

    buy_price = (
        pd.to_numeric(norm["buy_price"], errors="coerce")
        if "buy_price" in norm.columns
        else pd.Series(pd.NA, index=norm.index)
    )
    trade_return = (
        pd.to_numeric(norm["trade_return"], errors="coerce")
        if "trade_return" in norm.columns
        else pd.Series(pd.NA, index=norm.index)
    )
    close_price = (
        pd.to_numeric(norm["close"], errors="coerce")
        if "close" in norm.columns
        else pd.Series(pd.NA, index=norm.index)
    )
    stock_codes = (
        norm["stock_code"].astype(str).str.strip()
        if "stock_code" in norm.columns
        else pd.Series("", index=norm.index)
    )

    trades: list[dict[str, object]] = []
    price_eps = 1e-12
    entry_date: pd.Timestamp | None = None
    entry_price: float | None = None
    entry_code: str | None = None

    for idx in range(len(norm)):
        trade_date = pd.Timestamp(norm.at[idx, "trade_date"]).normalize()
        buy_val = buy_price.iloc[idx]
        if pd.notna(buy_val) and abs(float(buy_val)) > price_eps:
            entry_date = trade_date
            entry_price = float(buy_val)
            code_val = stock_codes.iloc[idx]
            entry_code = code_val if code_val and code_val.lower() != "nan" else None

        trade_ret_val = trade_return.iloc[idx]
        if pd.isna(trade_ret_val):
            continue

        code_val = entry_code
        if not code_val:
            row_code = stock_codes.iloc[idx]
            code_val = row_code if row_code and row_code.lower() != "nan" else ""
        trades.append(
            {
                "stock_code": code_val,
                "买入日": entry_date if entry_date is not None else trade_date,
                "卖出日": trade_date,
                "盈亏": float(trade_ret_val),
            }
        )
        entry_date = None
        entry_price = None
        entry_code = None

    if entry_date is not None and entry_price is not None and abs(entry_price) > price_eps:
        exit_date = pd.Timestamp(norm["trade_date"].iloc[-1]).normalize()
        exit_price = close_price.iloc[-1]
        if pd.notna(exit_price) and abs(float(exit_price)) > price_eps:
            final_return = float(exit_price) / entry_price - 1.0
        else:
            final_return = 0.0
        trades.append(
            {
                "stock_code": entry_code or "",
                "买入日": entry_date,
                "卖出日": exit_date,
                "盈亏": float(final_return),
            }
        )

    if not trades:
        return empty_trade_segments_df()
    out = pd.DataFrame(trades)
    out = ensure_trade_date_normalized(out, col="买入日")
    out = ensure_trade_date_normalized(out, col="卖出日")
    out["盈亏"] = pd.to_numeric(out["盈亏"], errors="coerce").fillna(0.0)
    return out.sort_values(["买入日", "stock_code", "卖出日"], kind="mergesort").reset_index(drop=True)


def build_trade_segments_from_processed_dfs(processed_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not processed_dfs:
        return empty_trade_segments_df()
    if len(processed_dfs) == 1:
        processed_df = next(iter(processed_dfs.values()))
        if processed_df is None or processed_df.empty:
            return empty_trade_segments_df()
        return build_trade_segments_from_processed_df(processed_df)

    all_trades: list[pd.DataFrame] = []
    for _name, processed_df in sorted(processed_dfs.items(), key=lambda kv: str(kv[0]).casefold()):
        if processed_df is None or processed_df.empty:
            continue
        trades_df = build_trade_segments_from_processed_df(processed_df)
        if trades_df is None or trades_df.empty:
            continue
        all_trades.append(trades_df)

    if not all_trades:
        return empty_trade_segments_df()
    out = pd.concat(all_trades, ignore_index=True)
    out["买入日"] = pd.to_datetime(out["买入日"], errors="coerce").dt.normalize()
    out["卖出日"] = pd.to_datetime(out["卖出日"], errors="coerce").dt.normalize()
    out["盈亏"] = pd.to_numeric(out["盈亏"], errors="coerce").fillna(0.0)
    return out.sort_values(["买入日", "stock_code", "卖出日"], kind="mergesort").reset_index(drop=True)


def build_trade_segments_summary_df(
    trades_df: pd.DataFrame,
    *,
    prefix: str,
) -> pd.DataFrame:
    if trades_df is None or trades_df.empty or "盈亏" not in trades_df.columns:
        return empty_trade_segments_summary_df(prefix=prefix)

    returns = pd.to_numeric(trades_df["盈亏"], errors="coerce").fillna(0.0)
    eps = 1e-8
    trade_cnt = int(len(returns))
    pos_cnt = int((returns > eps).sum())
    neg_cnt = int((returns < -eps).sum())
    zero_cnt = int((returns.abs() <= eps).sum())
    avg_trade_return = float(returns.mean()) if trade_cnt > 0 else 0.0
    win_rate = float(pos_cnt / trade_cnt) if trade_cnt > 0 else 0.0

    return pd.DataFrame(
        [
            {
                f"{prefix}交易笔数": trade_cnt,
                f"{prefix}收益率为正的交易笔数": pos_cnt,
                f"{prefix}收益率为负的交易笔数": neg_cnt,
                f"{prefix}收益率为0的交易笔数": zero_cnt,
                f"{prefix}平均每笔收益率": avg_trade_return,
                f"{prefix}胜率": win_rate,
            }
        ]
    )


__all__ = [
    "build_trade_segments_from_processed_df",
    "build_trade_segments_from_processed_dfs",
    "build_trade_segments_summary_df",
    "empty_trade_segments_df",
    "empty_trade_segments_summary_df",
]

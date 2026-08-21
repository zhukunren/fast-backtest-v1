"""
Shared TopN signal and trade-segment helpers.
"""
from __future__ import annotations

import pandas as pd

from core.constants import COL_STOCK_CODE, COL_TRADE_DATE
from core.exceptions import DataValidationError
from core.invariants import ensure_trade_date_normalized

from .evaluation.trade_segment_summary import build_trade_segments_summary_df


_DETAIL_COLS = [
    COL_TRADE_DATE,
    COL_STOCK_CODE,
    "buy_date",
    "sell_date",
    "trade_return",
    "segment_rows",
]


def empty_topn_trade_segments_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_DETAIL_COLS)


def empty_topn_trade_segments_summary_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "TopN后交易段交易笔数",
            "TopN后交易段收益率为正的交易笔数",
            "TopN后交易段收益率为负的交易笔数",
            "TopN后交易段收益率为0的交易笔数",
            "TopN后交易段平均每笔收益率",
            "TopN后交易段胜率",
        ]
    )


def normalize_topn_signal_df(
    df: pd.DataFrame | None,
    *,
    drop_empty_codes: bool = False,
) -> pd.DataFrame:
    if df is None or df.empty:
        if isinstance(df, pd.DataFrame):
            return pd.DataFrame(columns=list(df.columns))
        return pd.DataFrame(columns=[COL_TRADE_DATE, COL_STOCK_CODE])
    if not {COL_TRADE_DATE, COL_STOCK_CODE}.issubset(df.columns):
        raise DataValidationError("TopN 信号缺少 trade_date/stock_code 列。")

    out = df.copy()
    out = ensure_trade_date_normalized(out, col=COL_TRADE_DATE)

    out[COL_STOCK_CODE] = out[COL_STOCK_CODE].astype(str).str.strip()
    if drop_empty_codes:
        out = out.loc[out[COL_STOCK_CODE].ne("")]
        if out.empty:
            return pd.DataFrame(columns=list(df.columns))
    elif (out[COL_STOCK_CODE] == "").any():
        bad = (
            out.loc[out[COL_STOCK_CODE] == "", [COL_TRADE_DATE, COL_STOCK_CODE]]
            .head(5)
            .to_dict(orient="records")
        )
        raise DataValidationError(f"输入 df.stock_code 存在空值（示例：{bad}）")

    out = out.sort_values([COL_TRADE_DATE, COL_STOCK_CODE], kind="mergesort").reset_index(drop=True)
    out = out.drop_duplicates(subset=[COL_TRADE_DATE, COL_STOCK_CODE], keep="first").reset_index(drop=True)
    return out


def finalize_topn_trade_segments_df(trades_df: pd.DataFrame | None) -> pd.DataFrame:
    if trades_df is None or trades_df.empty:
        return empty_topn_trade_segments_df()

    out = pd.DataFrame(trades_df).copy()
    out = ensure_trade_date_normalized(out, col=COL_TRADE_DATE)
    out["buy_date"] = pd.to_datetime(out["buy_date"], errors="coerce").dt.normalize()
    out["sell_date"] = pd.to_datetime(out["sell_date"], errors="coerce").dt.normalize()
    out["trade_return"] = pd.to_numeric(out["trade_return"], errors="coerce").fillna(0.0)
    out["segment_rows"] = pd.to_numeric(out["segment_rows"], errors="coerce").fillna(0).astype(int)
    out = out.sort_values([COL_TRADE_DATE, COL_STOCK_CODE], kind="mergesort").reset_index(drop=True)
    return out.loc[:, _DETAIL_COLS]


def build_topn_trade_segments_summary_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df is None or trades_df.empty or "trade_return" not in trades_df.columns:
        return empty_topn_trade_segments_summary_df()
    summary_input = trades_df.rename(
        columns={
            "buy_date": "买入日",
            "sell_date": "卖出日",
            "trade_return": "盈亏",
        }
    )
    return build_trade_segments_summary_df(summary_input, prefix="TopN后交易段")


__all__ = [
    "build_topn_trade_segments_summary_df",
    "empty_topn_trade_segments_df",
    "empty_topn_trade_segments_summary_df",
    "finalize_topn_trade_segments_df",
    "normalize_topn_signal_df",
]

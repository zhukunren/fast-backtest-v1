from __future__ import annotations

from types import SimpleNamespace

import pandas as pd


RISK_FILL_COLUMNS = [
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
    "before_ratio",
    "after_ratio",
    "risk_reason",
    "risk_state",
    "dd_state",
]


def cost_config(
    *,
    enable: bool = False,
    commission_rate: float = 0.0,
    min_commission: float = 0.0,
    stamp_duty_rate: float = 0.0,
    slippage_bps: float = 0.0,
    include_buy_commission: bool = True,
    include_sell_commission: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        enable=enable,
        commission_rate=commission_rate,
        min_commission=min_commission,
        stamp_duty_rate=stamp_duty_rate,
        slippage_bps=slippage_bps,
        include_buy_commission=include_buy_commission,
        include_sell_commission=include_sell_commission,
    )


def raw_frame(
    dates: list[str],
    *,
    shares: list[int],
    closes: list[float],
    stock_code: str = "AAA.SZ",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(dates),
            "stock_code": [stock_code] * len(dates),
            "shares": shares,
            "close": closes,
        }
    )


def risk_frame(
    dates: list[str],
    *,
    raw_ratios: list[float],
    effective_ratios: list[float],
    risk_states: list[str] | None = None,
    dd_states: list[str] | None = None,
) -> pd.DataFrame:
    row_count = len(dates)
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(dates),
            "utilization_rate_raw": raw_ratios,
            "effective_position_ratio": effective_ratios,
            "risk_state": risk_states if risk_states is not None else [""] * row_count,
            "dd_state": dd_states if dd_states is not None else [""] * row_count,
        }
    )


def strategy_buy_fill(*, trade_date: str, qty: int, cash_delta: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "_slot_event_seq": 1,
                "trade_date": trade_date,
                "stock_code": "AAA.SZ",
                "side": "buy",
                "qty": qty,
                "cash_delta": cash_delta,
                "source": "strategy",
            }
        ]
    )

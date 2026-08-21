from __future__ import annotations

from dataclasses import replace

import pandas as pd

from config import get_config


class DummyStrategy:
    STRATEGY_META = {
        "sort_spec": ["stock_code"],
        "portfolio_num_j": 1,
        "portfolio_num_i": 1,
    }


def make_slot_broker_cfg():
    return make_slot_like_cfg("slot_broker")


def make_slot_like_cfg(engine: str = "slot_broker"):
    cfg = get_config()
    portfolio_cfg = replace(
        cfg.portfolio,
        engine=engine,
    )
    return replace(cfg, portfolio=portfolio_cfg)


def make_trading_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "stock_code": ["AAA.SZ", "AAA.SZ", "AAA.SZ"],
            "open": [10.0, 10.0, 10.0],
            "high": [10.0, 10.5, 11.0],
            "low": [10.0, 9.5, 9.8],
            "close": [10.0, 10.5, 11.0],
            "direction": ["", "buy", "sell"],
            "volume": [1000.0, 1000.0, 1000.0],
        }
    )


def make_processed_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "stock_code": ["AAA.SZ", "AAA.SZ", "AAA.SZ"],
            "open": [10.0, 10.0, 10.0],
            "close": [10.0, 10.5, 11.0],
            "position": [0, 1, 0],
            "shares": [0, 100, 0],
            "cash": [1000.0, 0.0, 1100.0],
            "net_value": [1.0, 1.05, 1.1],
            "utilization_rate": [0.0, 1.0, 0.0],
            "buy_price": [float("nan"), 10.0, float("nan")],
            "sell_price": [float("nan"), float("nan"), 11.0],
            "trade_return": [float("nan"), float("nan"), 0.1],
            "commission": [0.0, 0.0, 0.0],
            "stamp_tax": [0.0, 0.0, 0.0],
            "slippage_cost": [0.0, 0.0, 0.0],
            "total_cost": [0.0, 0.0, 0.0],
        }
    )

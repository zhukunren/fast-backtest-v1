from __future__ import annotations

import pandas as pd


def merge_options(engine_id: object) -> dict[str, object]:
    normalized = str(engine_id or "legacy").strip().lower()
    return {
        "accounting_mode": "cash" if normalized in {"slot", "slot_broker"} else "close",
        "collect_ledger_artifacts": normalized == "slot_broker",
    }


def make_trading_df(dates, with_dup: bool = False) -> pd.DataFrame:
    row_count = len(dates) + (1 if with_dup else 0)
    return pd.DataFrame(
        {
            "trade_date": dates + ([dates[-1]] if with_dup else []),
            "stock_code": ["AAA.SZ"] * row_count,
            "open": [10.0] * row_count,
            "high": [10.5] * row_count,
            "low": [9.8] * row_count,
            "close": [10.0] * row_count,
            "direction": [""] * row_count,
            "volume": [1000] * row_count,
        }
    )

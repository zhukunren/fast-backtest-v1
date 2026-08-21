from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd


def build_standard_price_frame(
    *,
    stock_code: str = "AAA.SZ",
    periods: int = 3,
    start: str = "2024-01-02",
    stock_name: str | None = "Alpha",
) -> pd.DataFrame:
    trade_days = pd.bdate_range(start=start, periods=int(periods))
    base = [10.0 + index for index in range(int(periods))]
    return build_price_frame(
        trade_days,
        stock_code=stock_code,
        stock_name=stock_name,
        open_=base,
        high=[value + 0.5 for value in base],
        low=[value - 0.5 for value in base],
        close=[value + 0.2 for value in base],
        volume=[1000 + index * 100 for index in range(int(periods))],
    )


def build_price_frame(
    trade_days: Iterable[object],
    *,
    stock_code: str,
    open_: Iterable[object],
    high: Iterable[object],
    low: Iterable[object],
    close: Iterable[object],
    volume: Iterable[object],
    stock_name: str | None = None,
) -> pd.DataFrame:
    trade_days_idx = pd.DatetimeIndex(pd.to_datetime(list(trade_days), errors="raise"))
    data: dict[str, object] = {
        "trade_date": trade_days_idx.strftime("%Y%m%d"),
        "stock_code": stock_code,
    }
    if stock_name is not None:
        data["stock_name"] = stock_name
    data.update(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return pd.DataFrame(data)


def write_local_market_dataset(
    sample_dir: Path,
    *,
    price_frames: Mapping[str, pd.DataFrame],
    stock_rows: Iterable[Mapping[str, object]],
) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    prices_dir = sample_dir / "prices"
    prices_dir.mkdir(parents=True, exist_ok=True)

    for raw_code in sorted(price_frames, key=str.casefold):
        code = str(raw_code or "").strip()
        if not code:
            continue
        price_frames[raw_code].to_csv(prices_dir / f"{code}.csv", index=False)

    stock_basic_df = pd.DataFrame(list(stock_rows))
    stock_basic_df.to_csv(sample_dir / "stock_basic.csv", index=False)


__all__ = ["build_price_frame", "build_standard_price_frame", "write_local_market_dataset"]

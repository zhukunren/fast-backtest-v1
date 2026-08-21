from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def stub_tushare_trade_calendar(monkeypatch, trade_days: Iterable[object]) -> None:
    """Install an explicit in-process TuShare calendar double for a test."""
    import data.trade_calendar as trade_calendar

    days = pd.DatetimeIndex(pd.to_datetime(list(trade_days), errors="raise")).normalize()
    days = days.drop_duplicates().sort_values()

    class _Client:
        def trade_cal(
            self,
            *,
            exchange: str,
            start_date: str,
            end_date: str,
            is_open: str,
        ) -> pd.DataFrame:
            del exchange
            if is_open != "1":
                raise AssertionError(f"expected open-day query, got is_open={is_open!r}")
            start = pd.to_datetime(start_date, format="%Y%m%d", errors="raise").normalize()
            end = pd.to_datetime(end_date, format="%Y%m%d", errors="raise").normalize()
            selected = days[(days >= start) & (days <= end)]
            return pd.DataFrame({"cal_date": selected.strftime("%Y%m%d")})

    monkeypatch.setattr(trade_calendar, "create_tushare_client", lambda token: _Client())


__all__ = ["stub_tushare_trade_calendar"]

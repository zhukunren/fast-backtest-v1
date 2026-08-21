"""
Shared helpers for deterministic test fixtures.
"""

__all__ = [
    "build_price_frame",
    "stub_tushare_trade_calendar",
    "temporary_env",
    "write_local_market_dataset",
]


def __getattr__(name: str):
    if name == "build_price_frame":
        from .local_data import build_price_frame

        return build_price_frame
    if name == "temporary_env":
        from .env import temporary_env

        return temporary_env
    if name == "stub_tushare_trade_calendar":
        from .tushare import stub_tushare_trade_calendar

        return stub_tushare_trade_calendar
    if name == "write_local_market_dataset":
        from .local_data import write_local_market_dataset

        return write_local_market_dataset
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

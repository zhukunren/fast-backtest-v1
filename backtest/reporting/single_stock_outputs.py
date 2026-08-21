"""Write single-stock backtest artifacts at the reporting boundary."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _write_csv(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def write_single_stock_outputs(
    *,
    cfg,
    stock_code: str,
    signal_payload: pd.DataFrame,
    price_df: pd.DataFrame,
    trades_df: pd.DataFrame,
) -> tuple[Path, ...]:
    """Persist all single-stock frames after computation has completed."""
    return (
        _write_csv(signal_payload, Path(cfg.paths.signal_data) / f"{stock_code}_signal.csv"),
        _write_csv(price_df, Path(cfg.paths.stock_data) / f"{stock_code}_data.csv"),
        _write_csv(trades_df, Path(cfg.paths.trade_data) / f"{stock_code}_trades.csv"),
    )


__all__ = ["write_single_stock_outputs"]

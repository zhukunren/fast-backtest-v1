from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def make_broker_cfg(
    tmp_path: Path,
    *,
    allow_missing_stock_data: bool = False,
    max_gross_exposure: float = 1.0,
    max_single_position_pct: float = 1.0,
    enable_no_trade_day: bool = False,
):
    paths = SimpleNamespace(
        stock_data=tmp_path / "stock_data",
        signal_data=tmp_path / "signal_data",
        net_value_unique_date=tmp_path / "net_value_unique_date",
    )
    paths.stock_data.mkdir(parents=True, exist_ok=True)
    paths.signal_data.mkdir(parents=True, exist_ok=True)
    paths.net_value_unique_date.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(
        run=SimpleNamespace(
            allow_missing_stock_data=allow_missing_stock_data,
            topn_candidate_factor=1.0,
        ),
        paths=paths,
        portfolio=SimpleNamespace(
            initial_capital=1000.0,
            lot_size=1,
            allow_partial_fill=True,
            cash_buffer_pct=0.0,
            position_size_method="equal",
            max_positions=2,
            max_single_position_pct=max_single_position_pct,
            max_gross_exposure=max_gross_exposure,
        ),
        transaction_cost=SimpleNamespace(
            enable=False,
            commission_rate=0.0,
            min_commission=0.0,
            stamp_duty_rate=0.0,
            slippage_bps=0.0,
            include_buy_commission=True,
            include_sell_commission=True,
        ),
        trade_constraints=SimpleNamespace(
            enable_no_trade_day=enable_no_trade_day,
            enable_limit=False,
            limit_up_pct=0.098,
            limit_down_pct=0.098,
            enable_volume_limit=False,
            max_participation_rate=1.0,
            require_next_bar=True,
        ),
    )


def make_broker_spec():
    return SimpleNamespace(
        sort_spec=[("score", False)],
        portfolio_num_j=1,
        portfolio_num_i=2,
    )


def make_execution_policy(
    *,
    allow_intraday: bool = False,
    shift_t1_sell: bool = False,
    sell_timing: str = "signal_close",
):
    return SimpleNamespace(
        buy_timing="next_open",
        sell_timing=sell_timing,
        shift_t1_sell=shift_t1_sell,
        allow_intraday=allow_intraday,
    )

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from config import get_config
from core.universe import UniverseResolution, build_config_universe_spec
from strategies.spec import RiskControlSpec, StrategySpec


def build_strategy_spec() -> StrategySpec:
    return StrategySpec(
        top_n=2,
        sort_spec=(("score", False),),
        price_columns=("open", "close"),
        use_index_timing=False,
        use_board_timing=False,
        board_index_codes=(),
        require_board_member=False,
        portfolio_num_j=2,
        portfolio_num_i=1,
    )


def build_risk_control_spec() -> RiskControlSpec:
    return RiskControlSpec(
        enable_nv=False,
        enable_dd=False,
        warn_dd=-0.10,
        danger_dd=-0.20,
        lock_dd=-0.30,
        nv_warn=0.9,
        nv_danger=0.8,
        nv_clear=0.5,
        nv_ratios=(1.0, 0.8, 0.5, 0.3),
        dd_ratios=(1.0, 0.8, 0.5, 0.3),
    )


def build_run_protocol_context(
    tmp_path: Path,
    *,
    strategy_file: Path | None = None,
) -> SimpleNamespace:
    cfg = get_config(reload=True)
    output_root = tmp_path / "run_out"
    strategy_module = (
        str(strategy_file)
        if strategy_file is not None
        else "strategy_library.builtin.breakout_20"
    )
    cfg = replace(
        cfg,
        paths=replace(
            cfg.paths,
            data_root=tmp_path,
            signal_data=output_root / "signal_data",
            stock_data=output_root / "stock_data",
            trade_data=output_root / "trade_data",
            return_date=output_root / "return_date",
            return_date_with_returns=output_root / "return_date_with_returns",
            net_value_unique_date=output_root / "net_value_unique_date",
        ),
        strategy=replace(cfg.strategy, module=strategy_module),
        diagnostics=replace(cfg.diagnostics, enabled=True, multi_start_enabled=False),
        transaction_cost=replace(cfg.transaction_cost, enable=False),
    )
    strategy_spec = build_strategy_spec()
    universe_resolution = UniverseResolution(
        spec=build_config_universe_spec(cfg=cfg, strategy_spec=strategy_spec),
        stock_code_list=(),
        source="config.backtest",
    )
    return SimpleNamespace(
        cfg=cfg,
        start_ts=time.time() - 1.0,
        run_id="run_protocol_test",
        output_root=output_root,
        run_meta_dir=output_root / "run_meta",
        tag_log_path=output_root / "logs" / "backtest_run_protocol_test.log",
        logger=logging.getLogger("test_run_protocol_artifacts"),
        raw_print=lambda *args: None,
        warn_collector=None,
        execution_policy=SimpleNamespace(
            to_dict=lambda: {
                "buy_timing": "next_open",
                "sell_timing": "signal_close",
                "shift_t1_sell": True,
                "allow_intraday": False,
            }
        ),
        strategy_spec=strategy_spec,
        risk_cfg=build_risk_control_spec(),
        universe_resolution=universe_resolution,
        meta=None,
        diagnostic_report=None,
        diagnostic_path=None,
        event_logger=None,
    )


__all__ = [
    "build_risk_control_spec",
    "build_run_protocol_context",
    "build_strategy_spec",
]

"""
Backtest shared context.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from core.run_metadata import create_run_id

if TYPE_CHECKING:
    from backtest.netvalue.positions import NetValueRuntime
    from backtest.trade_state_machine import TradeStateRuntime
    from config.models import AppConfig
    from core.execution_policy import ExecutionPolicy
    from core.market_timing import BoardMemberCache, IndexTimingCache
    from backtest.artifacts import ArtifactPolicy
    from strategies.protocol import StrategyLike
    from strategies.descriptor import LoadedStrategyDescriptor
    from strategies.point_in_time import StrategyDataInputs
    from strategies.spec import RiskControlSpec, StrategySpec
    from core.universe import UniverseResolution
    from data.trade_calendar import TradeCalendarCache
    from backtest.signal.board_cache import BoardTimeCache


@dataclass(frozen=True)
class BacktestContext:
    cfg: AppConfig
    spec: StrategySpec | None
    execution_policy: ExecutionPolicy
    risk_cfg: RiskControlSpec | None
    artifact_policy: ArtifactPolicy
    timing_cache: IndexTimingCache
    board_member_cache: BoardMemberCache
    board_time_cache: BoardTimeCache
    trade_state_runtime: TradeStateRuntime
    trade_calendar_cache: TradeCalendarCache
    net_value_runtime: NetValueRuntime
    stock_code_list: tuple[str, ...] | None = None
    universe_resolution: UniverseResolution | None = None
    strategy: StrategyLike | None = None
    strategy_descriptor: LoadedStrategyDescriptor | None = None
    strategy_data_inputs: StrategyDataInputs | None = None
    logger: logging.Logger | None = None
    run_id: str | None = None
    defer_artifact_publish: bool = False

def create_backtest_context(
    *,
    cfg: AppConfig,
    spec: StrategySpec | None,
    execution_policy: ExecutionPolicy,
    risk_cfg: RiskControlSpec | None = None,
    artifact_policy: ArtifactPolicy | str = "none",
    timing_cache: IndexTimingCache | None = None,
    board_member_cache: BoardMemberCache | None = None,
    board_time_cache: BoardTimeCache | None = None,
    trade_state_runtime: TradeStateRuntime | None = None,
    trade_calendar_cache: TradeCalendarCache | None = None,
    net_value_runtime: NetValueRuntime | None = None,
    stock_code_list: tuple[str, ...] | None = None,
    universe_resolution: UniverseResolution | None = None,
    strategy: StrategyLike | None = None,
    strategy_descriptor: LoadedStrategyDescriptor | None = None,
    strategy_data_inputs: StrategyDataInputs | None = None,
    logger: logging.Logger | None = None,
    run_id: str | None = None,
    defer_artifact_publish: bool = False,
) -> BacktestContext:
    from backtest.netvalue.positions import create_net_value_runtime
    from backtest.signal.board_cache import create_board_time_cache
    from backtest.trade_state_machine import create_trade_state_runtime
    from core.market_timing import create_index_timing_cache
    from data.trade_calendar import create_trade_calendar_cache
    from data.timing_readers import create_board_member_cache

    from backtest.artifacts import ArtifactPolicy

    return BacktestContext(
        cfg=cfg,
        spec=spec,
        execution_policy=execution_policy,
        risk_cfg=risk_cfg,
        artifact_policy=ArtifactPolicy.coerce(artifact_policy),
        timing_cache=timing_cache if timing_cache is not None else create_index_timing_cache(),
        board_member_cache=board_member_cache if board_member_cache is not None else create_board_member_cache(),
        board_time_cache=board_time_cache if board_time_cache is not None else create_board_time_cache(),
        trade_state_runtime=(
            trade_state_runtime if trade_state_runtime is not None else create_trade_state_runtime()
        ),
        trade_calendar_cache=(
            trade_calendar_cache if trade_calendar_cache is not None else create_trade_calendar_cache()
        ),
        net_value_runtime=net_value_runtime if net_value_runtime is not None else create_net_value_runtime(),
        stock_code_list=stock_code_list,
        universe_resolution=universe_resolution,
        strategy=strategy,
        strategy_descriptor=strategy_descriptor,
        strategy_data_inputs=strategy_data_inputs,
        logger=logger,
        run_id=str(run_id or "").strip() or create_run_id(),
        defer_artifact_publish=bool(defer_artifact_publish),
    )

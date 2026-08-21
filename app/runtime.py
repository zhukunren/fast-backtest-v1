from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from config import AppConfig
from core.execution_policy import ExecutionPolicy
from core.run_events import JsonlRunEventLogger
from strategies.spec import RiskControlSpec, StrategySpec
from core.universe import UniverseResolution

from .console import WarningCollector

if TYPE_CHECKING:
    from strategies.authoring.requirements import StrategyRequirements
    from strategies.descriptor import LoadedStrategyDescriptor


@dataclass
class RuntimeContext:
    cfg: AppConfig
    start_ts: float
    run_id: str
    output_root: Path
    run_meta_dir: Path
    tag_log_path: Path
    logger: logging.Logger
    raw_print: Callable[..., None]
    warn_collector: WarningCollector
    execution_policy: ExecutionPolicy | None = None
    strategy_spec: StrategySpec | None = None
    strategy_descriptor: LoadedStrategyDescriptor | None = None
    strategy_runtime_requirements: StrategyRequirements | None = None
    risk_cfg: RiskControlSpec | None = None
    universe_resolution: UniverseResolution | None = None
    meta: dict[str, Any] | None = None
    diagnostic_report: dict[str, Any] | None = None
    diagnostic_path: Path | None = None
    event_logger: JsonlRunEventLogger | None = None

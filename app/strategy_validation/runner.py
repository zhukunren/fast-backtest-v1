from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Mapping

from backtest.result import CurvePayload, RunResult
from config import AppConfig, apply_config_overrides

from ..backtest_session_service import BacktestSessionOutcome
from ..diagnostics.snapshot import build_diagnostic_snapshot
from ..session_metadata import read_session_meta_reason
from .common import ensure_dict, slugify
from .models import StrategyValidationCase, StrategyValidationProtocol

ValidationSession = Callable[..., BacktestSessionOutcome]


def default_output_root(cfg: AppConfig, *, run_id: str, module: str) -> Path:
    module_slug = slugify(str(module).split(".")[-1])
    return Path(cfg.paths.repo_root) / "output" / "tools" / "strategy_validation" / f"{run_id}_{module_slug}"


def _case_path_overrides(case_root: Path) -> dict[str, Any]:
    return {
        "paths.signal_data": case_root / "signal_data",
        "paths.stock_data": case_root / "stock_data",
        "paths.trade_data": case_root / "trade_data",
        "paths.return_date": case_root / "return_date",
        "paths.return_date_with_returns": case_root / "return_date_with_returns",
        "paths.net_value_unique_date": case_root / "net_value_unique_date",
    }


def _result_payload(result: Any) -> dict[str, Any]:
    if not isinstance(result, RunResult) or not isinstance(result.payload, CurvePayload):
        return {}
    payload = result.payload
    metrics = payload.metrics
    final_net_value = metrics.final_net_value if metrics is not None else None
    max_drawdown = metrics.max_drawdown if metrics is not None else None
    statistical: dict[str, Any] = {
        "daily_return_count": 0,
        "mean_daily_return": None,
        "daily_return_std": None,
        "annualized_sharpe": None,
        "positive_mean_p_value": None,
    }
    net_value_df = payload.equity_curve
    if hasattr(net_value_df, "columns") and len(net_value_df) > 1:
        value_col = next(
            (name for name in ("sum_net_value", "net_value", "portfolio_value") if name in net_value_df.columns),
            None,
        )
        if value_col is not None:
            returns = net_value_df[value_col].astype(float).pct_change().replace([float("inf"), float("-inf")], float("nan")).dropna()
            count = int(len(returns))
            mean = float(returns.mean()) if count else None
            std = float(returns.std(ddof=1)) if count > 1 else None
            p_value = None
            sharpe = None
            if count >= 20 and std is not None and std > 0.0 and mean is not None:
                z_score = mean / (std / math.sqrt(count))
                p_value = float(1.0 - NormalDist().cdf(z_score))
                sharpe = float(mean / std * math.sqrt(252.0))
            statistical = {
                "daily_return_count": count,
                "mean_daily_return": mean,
                "daily_return_std": std,
                "annualized_sharpe": sharpe,
                "positive_mean_p_value": p_value,
            }
    return {
        "final_net_value": float(final_net_value) if final_net_value is not None else None,
        "cum_return": (float(final_net_value) - 1.0) if final_net_value is not None else None,
        "max_drawdown": float(max_drawdown) if max_drawdown is not None else None,
        "signal_stock_count": int(result.request.signal_stock_count),
        "actual_segment_trade_count": int(metrics.actual_segment_trade_count if metrics is not None else 0),
        "actual_segment_avg_trade_return": float(
            metrics.actual_segment_avg_trade_return if metrics is not None else 0.0
        ),
        "actual_segment_win_rate": float(metrics.actual_segment_win_rate if metrics is not None else 0.0),
        "duration_sec": float(getattr(result, "duration_sec", 0.0) or 0.0),
        **statistical,
    }


def _serialize_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        out[str(key)] = str(value) if isinstance(value, Path) else value
    return out


def _strategy_contract_check(descriptor: Any, *, strict: bool) -> dict[str, Any]:
    if descriptor is None:
        return {
            "ok": None,
            "status": "not_evaluated",
            "strict": bool(strict),
            "evaluated": False,
            "issues": [],
        }
    try:
        from strategies.api import validate_strategy_contract

        report = dict(validate_strategy_contract(descriptor.spec, strict=bool(strict)))
        report["evaluated"] = True
        return report
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "strict": bool(strict),
            "evaluated": True,
            "issues": [
                {
                    "severity": "error",
                    "code": "strategy_contract_validation_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            ],
        }


def run_validation_case(
    *,
    session: ValidationSession,
    base_cfg: AppConfig,
    module: str,
    protocol: StrategyValidationProtocol,
    case: StrategyValidationCase,
    root_dir: Path,
    strict_contract: bool = False,
) -> dict[str, Any]:
    case_root = root_dir / "cases" / case.name
    defaults_overrides = ensure_dict(protocol.defaults.get("overrides"), label="protocol.defaults.overrides")
    merged_overrides = dict(defaults_overrides)
    merged_overrides.update(case.overrides)
    merged_overrides.update(_case_path_overrides(case_root))
    merged_overrides["strategy.module"] = module
    if case.start_time:
        merged_overrides["backtest.start_time"] = case.start_time
    if case.end_time:
        merged_overrides["backtest.end_time"] = case.end_time
    cfg = apply_config_overrides(base_cfg, merged_overrides)

    outcome = session(
        cfg,
        raw_print=lambda *args, **kwargs: None,
        print_summary=False,
        stock_code_list=case.stock_code_list,
    )
    run_meta_path = outcome.ctx.run_meta_dir / f"{outcome.ctx.run_id}.json"
    diagnostic_path = outcome.ctx.diagnostic_path
    meta: dict[str, Any] = {}
    if run_meta_path.exists():
        loaded = json.loads(run_meta_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            meta = loaded
    elif isinstance(outcome.ctx.meta, dict):
        meta = dict(outcome.ctx.meta)

    diagnostics_report = outcome.ctx.diagnostic_report if isinstance(outcome.ctx.diagnostic_report, dict) else {}
    diagnostics_snapshot = build_diagnostic_snapshot(diagnostics_report)
    error_reason = ""
    if outcome.result is None:
        error_reason = read_session_meta_reason(meta, default="backtest_session_failed")
    contract_check = _strategy_contract_check(
        getattr(outcome.ctx, "strategy_descriptor", None),
        strict=bool(strict_contract),
    )

    return {
        "name": case.name,
        "description": case.description,
        "gate_required": bool(case.gate_required),
        "status": "completed" if outcome.result is not None else "failed",
        "error": error_reason,
        "window": {
            "start_time": cfg.backtest.start_time,
            "end_time": cfg.backtest.end_time,
        },
        "overrides": _serialize_mapping(merged_overrides),
        "stock_code_list": list(case.stock_code_list or ()),
        "result": _result_payload(outcome.result),
        "contract_check": contract_check,
        "diagnostics": diagnostics_report,
        "diagnostics_snapshot": diagnostics_snapshot,
        "artifacts": {
            "case_root": str(case_root),
            "run_id": outcome.ctx.run_id,
            "log_path": str(outcome.ctx.tag_log_path),
            "run_meta_path": str(run_meta_path) if run_meta_path.exists() else "",
            "diagnostics_path": str(diagnostic_path) if diagnostic_path is not None else "",
        },
        "meta": meta,
    }


__all__ = ["default_output_root", "run_validation_case"]

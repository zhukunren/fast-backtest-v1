"""Write schema-v2 run artifacts to configured output folders."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from core.artifact_schema import MARKET_STATE_SCHEMA_VERSION, artifact_schema_fields
from core.logging import log_info

from backtest.artifacts.generation import ArtifactSource
from backtest.decisions import SignalDecisionArtifacts
from backtest.pipeline_result import TradeSegmentsBuildResult
from backtest.result import AssessPayload, CurvePayload, RunResult, SinglePayload

from .benchmark import build_benchmark_curves_from_net_value_payload
from .market_state import compute_market_state_from_net_value_payload, summarize_market_state_payload
from .stage_outputs import write_execution_outputs


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_csv(df: pd.DataFrame, path: Path, *, encoding: str | None = None) -> Path:
    _ensure_parent(path)
    kwargs = {"index": False}
    if encoding is not None:
        kwargs["encoding"] = encoding
    df.to_csv(path, **kwargs)
    return path


def _write_if_frame(
    df: pd.DataFrame | None,
    path: Path,
    *,
    relative_path: str,
    catalog_key: str,
    encoding: str | None = None,
) -> ArtifactSource | None:
    if not isinstance(df, pd.DataFrame):
        return None
    return ArtifactSource(
        path=_write_csv(df, path, encoding=encoding),
        relative_path=relative_path,
        catalog_key=catalog_key,
    )


def _candidate_outputs_enabled(cfg) -> bool:
    raw = getattr(getattr(cfg, "run", None), "topn_candidate_factor", 1.0)
    return float(raw or 1.0) > 1.0


def _resolved_topn_frame(signals: SignalDecisionArtifacts) -> pd.DataFrame | None:
    if isinstance(signals.selected_df, pd.DataFrame):
        return signals.selected_df
    if isinstance(signals.candidate_df, pd.DataFrame):
        return signals.candidate_df.head(0).copy()
    return None


def _write_signal_artifacts(
    *,
    cfg,
    signals: SignalDecisionArtifacts | None,
) -> tuple[ArtifactSource, ...]:
    if not isinstance(signals, SignalDecisionArtifacts):
        return ()
    signal_dir = Path(cfg.paths.signal_data)
    output_root = signal_dir.parent
    encoding = "utf-8-sig"
    written = [
        _write_if_frame(
            signals.backtest_summary_df,
            output_root / "backtest_summary.csv",
            relative_path="backtest_summary.csv",
            catalog_key="backtest_summary",
        ),
        _write_if_frame(
            signals.input_report_df,
            signal_dir / "signal_input_report.csv",
            relative_path="signal_data/signal_input_report.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
        _write_if_frame(
            signals.daily_summary_df,
            signal_dir / "signal_daily_summary.csv",
            relative_path="signal_data/signal_daily_summary.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
        _write_if_frame(
            _resolved_topn_frame(signals),
            signal_dir / "merged_signals.csv",
            relative_path="signal_data/merged_signals.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
    ]
    if _candidate_outputs_enabled(cfg):
        written.append(
            _write_if_frame(
                signals.candidate_df,
                signal_dir / "merged_signals_candidates.csv",
                relative_path="signal_data/merged_signals_candidates.csv",
                catalog_key="decision_artifacts",
                encoding=encoding,
            )
        )
    return tuple(path for path in written if path is not None)


def _write_topn_segments(
    *,
    cfg,
    segments: TradeSegmentsBuildResult | None,
) -> tuple[ArtifactSource, ...]:
    if not isinstance(segments, TradeSegmentsBuildResult):
        return ()
    signal_dir = Path(cfg.paths.signal_data)
    encoding = "utf-8-sig"
    written = (
        _write_if_frame(
            segments.all_trade_dates_df,
            signal_dir / "all_trade_dates.csv",
            relative_path="signal_data/all_trade_dates.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
        _write_if_frame(
            segments.report_df,
            signal_dir / "all_trade_dates_report.csv",
            relative_path="signal_data/all_trade_dates_report.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
        _write_if_frame(
            segments.topn_trade_segments_df,
            signal_dir / "topn_trade_segments.csv",
            relative_path="signal_data/topn_trade_segments.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
        _write_if_frame(
            segments.topn_trade_segments_summary_df,
            signal_dir / "topn_trade_segments_summary.csv",
            relative_path="signal_data/topn_trade_segments_summary.csv",
            catalog_key="decision_artifacts",
            encoding=encoding,
        ),
    )
    return tuple(path for path in written if path is not None)


def _market_state_input(equity_curve: pd.DataFrame | None) -> dict[str, Any] | None:
    if not isinstance(equity_curve, pd.DataFrame):
        return None
    if "trade_date" not in equity_curve.columns or "sum_net_value" not in equity_curve.columns:
        return None
    work = equity_curve.loc[:, ["trade_date", "sum_net_value"]].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work["sum_net_value"] = pd.to_numeric(work["sum_net_value"], errors="coerce")
    work = (
        work.dropna(subset=["trade_date", "sum_net_value"])
        .sort_values("trade_date", kind="mergesort")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
    )
    if work.empty:
        return None
    return {
        "dates": work["trade_date"].dt.strftime("%Y-%m-%d").tolist(),
        "net_value": [round(float(value), 6) for value in work["sum_net_value"].tolist()],
    }


def _write_market_state(*, cfg, equity_curve: pd.DataFrame | None) -> ArtifactSource | None:
    payload_input = _market_state_input(equity_curve)
    if isinstance(payload_input, dict):
        benchmark = build_benchmark_curves_from_net_value_payload(payload_input, cfg=cfg)
        if benchmark:
            payload_input = {**payload_input, **benchmark}
    payload = compute_market_state_from_net_value_payload(payload_input) if payload_input else None
    log_info(summarize_market_state_payload(payload))
    if not isinstance(payload, dict):
        return None
    output = {
        **artifact_schema_fields("market_state", MARKET_STATE_SCHEMA_VERSION),
        **payload,
    }
    path = Path(cfg.paths.signal_data).parent / "market_state.json"
    _ensure_parent(path)
    path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    return ArtifactSource(
        path=path,
        relative_path="market_state.json",
        catalog_key="market_state",
    )


def _single_stock_sources(cfg, result: RunResult) -> tuple[ArtifactSource, ...]:
    if not isinstance(result.payload, SinglePayload) or result.request.signal_stock_count <= 0:
        return ()
    stock_code = str(cfg.backtest.default_stock_code)
    candidates = (
        (
            Path(cfg.paths.signal_data) / f"{stock_code}_signal.csv",
            f"signal_data/{stock_code}_signal.csv",
            "single_signal",
        ),
        (
            Path(cfg.paths.stock_data) / f"{stock_code}_data.csv",
            f"stock_data/{stock_code}_data.csv",
            "single_price",
        ),
        (
            Path(cfg.paths.trade_data) / f"{stock_code}_trades.csv",
            f"trade_data/{stock_code}_trades.csv",
            "single_trades",
        ),
    )
    return tuple(
        ArtifactSource(path=path, relative_path=relative_path, catalog_key=catalog_key)
        for path, relative_path, catalog_key in candidates
        if path.is_file()
    )


def _ordered_unique_sources(sources: list[ArtifactSource]) -> tuple[ArtifactSource, ...]:
    by_path = {
        source.relative_path: source
        for source in sources
        if source.path.is_file()
    }
    return tuple(by_path[key] for key in sorted(by_path, key=str.casefold))


def write_run_result_outputs(*, cfg, result: RunResult) -> tuple[ArtifactSource, ...]:
    if not isinstance(result, RunResult):
        return ()
    written: list[ArtifactSource] = []
    payload = result.payload
    if isinstance(payload, (CurvePayload, AssessPayload)):
        signals = payload.decision_artifacts
        if isinstance(signals, SignalDecisionArtifacts):
            written.extend(_write_signal_artifacts(cfg=cfg, signals=signals))
            written.extend(_write_topn_segments(cfg=cfg, segments=signals.topn_segments))
        written.extend(write_execution_outputs(cfg=cfg, execution=payload.execution))
    if isinstance(payload, CurvePayload):
        market_state_source = _write_market_state(cfg=cfg, equity_curve=payload.equity_curve)
        if market_state_source is not None:
            written.append(market_state_source)
    written.extend(_single_stock_sources(cfg, result))
    strategy_spec_path = Path(cfg.paths.signal_data) / "strategy_spec.json"
    if result.identity.strategy_name is not None and strategy_spec_path.is_file():
        written.append(
            ArtifactSource(
                path=strategy_spec_path,
                relative_path="signal_data/strategy_spec.json",
                catalog_key="strategy_spec",
            )
        )
    return _ordered_unique_sources(written)


__all__ = ["write_run_result_outputs"]

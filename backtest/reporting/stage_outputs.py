"""Write explicit engine-execution artifacts to configured output folders."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from core.logging import log_info

from backtest.artifacts.generation import ArtifactSource
from backtest.result import (
    ExecutionArtifacts,
    LedgerExecutionArtifacts,
    RebalanceExecutionArtifacts,
    VectorizedExecutionArtifacts,
)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_csv(df: pd.DataFrame | None, path: Path, *, encoding: str | None = None) -> Path:
    _ensure_parent(path)
    frame = df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    kwargs = {"index": False}
    if encoding is not None:
        kwargs["encoding"] = encoding
    frame.to_csv(path, **kwargs)
    return path


def _write_named_frames(
    frames: Mapping[str, pd.DataFrame] | None,
    *,
    output_dir: Path,
    relative_dir: str,
    catalog_key: str,
    suffix: str = "",
) -> tuple[ArtifactSource, ...]:
    if not isinstance(frames, Mapping):
        return ()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[ArtifactSource] = []
    for name, frame in sorted(frames.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(frame, pd.DataFrame):
            continue
        filename = f"{name}{suffix}.csv"
        path = _write_csv(frame, output_dir / filename)
        written.append(
            ArtifactSource(
                path=path,
                relative_path=f"{relative_dir}/{filename}",
                catalog_key=catalog_key,
            )
        )
    return tuple(written)


def _merge_ledger_frames(
    base_df: pd.DataFrame,
    risk_df: pd.DataFrame,
) -> pd.DataFrame:
    base = base_df.copy() if isinstance(base_df, pd.DataFrame) else pd.DataFrame()
    risk = risk_df.copy() if isinstance(risk_df, pd.DataFrame) else pd.DataFrame()
    if "source" in base.columns and not risk.empty:
        base = base.loc[~base["source"].astype(str).str.strip().str.lower().eq("risk")].copy()
    frames = [frame for frame in (base, risk) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    merged["_seq"] = pd.Series(range(len(merged)), index=merged.index, dtype="int64")
    sort_columns = ["_seq"]
    if "trade_date" in merged.columns:
        merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.normalize()
        sort_columns.insert(0, "trade_date")
    if "order_id" in merged.columns:
        order_number = merged["order_id"].astype(str).str.extract(r"(\d+)$", expand=False)
        merged["_order_number"] = pd.to_numeric(order_number, errors="coerce")
        sort_columns.insert(-1, "_order_number")
    merged = merged.sort_values(sort_columns, kind="mergesort")
    return merged.drop(columns=["_seq", "_order_number"], errors="ignore").reset_index(drop=True)


def _common_execution_frames(
    execution: ExecutionArtifacts,
) -> tuple[
    Mapping[str, pd.DataFrame] | None,
    Mapping[str, pd.DataFrame] | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
    pd.DataFrame | None,
]:
    if isinstance(execution, (VectorizedExecutionArtifacts, LedgerExecutionArtifacts)):
        return (
            execution.trading_frames,
            execution.processed_frames,
            execution.equity_curve,
            execution.trade_segments_summary_df,
            execution.risk_control_summary_df,
            execution.risk_control_events_df,
        )
    return None, None, execution.equity_curve, None, None, None


def _fixed_source(path: Path, *, catalog_key: str) -> ArtifactSource:
    return ArtifactSource(
        path=path,
        relative_path=f"net_value_unique_date/{path.name}",
        catalog_key=catalog_key,
    )


def write_execution_outputs(*, cfg, execution: ExecutionArtifacts | None) -> tuple[ArtifactSource, ...]:
    if execution is None:
        return ()

    written: list[ArtifactSource] = []

    trading_frames, processed_frames, equity_curve, segments_summary, risk_summary, risk_events = (
        _common_execution_frames(execution)
    )
    trading_paths = _write_named_frames(
        trading_frames,
        output_dir=Path(cfg.paths.return_date),
        relative_dir="return_date",
        catalog_key="trading_frames",
    )
    written.extend(trading_paths)
    if trading_paths:
        log_info(f"滚动交易列表已保存 rows={len(trading_paths)}")
    processed_paths = _write_named_frames(
        processed_frames,
        output_dir=Path(cfg.paths.return_date_with_returns),
        relative_dir="return_date_with_returns",
        catalog_key="processed_frames",
        suffix="_with_returns",
    )
    written.extend(processed_paths)
    if processed_paths:
        log_info(f"持仓收益明细已保存 rows={len(processed_paths)}")

    output_dir = Path(cfg.paths.net_value_unique_date)
    if isinstance(equity_curve, pd.DataFrame):
        written.append(
            _fixed_source(
                _write_csv(equity_curve, output_dir / "merged_net_value_with_sum.csv"),
                catalog_key="net_value",
            )
        )
    if isinstance(segments_summary, pd.DataFrame) and not segments_summary.empty:
        written.append(
            _fixed_source(
                _write_csv(
                    segments_summary,
                    output_dir / "trade_segments_summary.csv",
                    encoding="utf-8-sig",
                ),
                catalog_key="execution_summary",
            )
        )
    if isinstance(risk_summary, pd.DataFrame) and not risk_summary.empty:
        written.append(
            _fixed_source(
                _write_csv(risk_summary, output_dir / "risk_control_summary.csv"),
                catalog_key="execution_summary",
            )
        )
    if isinstance(risk_events, pd.DataFrame) and not risk_events.empty:
        written.append(
            _fixed_source(
                _write_csv(risk_events, output_dir / "risk_control_events.csv"),
                catalog_key="execution_summary",
            )
        )

    if isinstance(execution, LedgerExecutionArtifacts):
        if isinstance(execution.daily_state_df, pd.DataFrame):
            written.append(
                _fixed_source(
                    _write_csv(execution.daily_state_df, output_dir / "portfolio_daily_state.csv"),
                    catalog_key="execution_ledger",
                )
            )
        written.append(
            _fixed_source(
                _write_csv(
                    _merge_ledger_frames(execution.orders_df, execution.risk_orders_df),
                    output_dir / "portfolio_orders.csv",
                ),
                catalog_key="execution_ledger",
            )
        )
        written.append(
            _fixed_source(
                _write_csv(execution.rejections_df, output_dir / "portfolio_rejections.csv"),
                catalog_key="execution_ledger",
            )
        )
        written.append(
            _fixed_source(
                _write_csv(
                    _merge_ledger_frames(execution.fills_df, execution.risk_fills_df),
                    output_dir / "portfolio_fills.csv",
                ),
                catalog_key="execution_ledger",
            )
        )
    elif isinstance(execution, RebalanceExecutionArtifacts):
        written.append(
            _fixed_source(
                _write_csv(execution.daily_state_df, output_dir / "portfolio_daily_state.csv"),
                catalog_key="execution_ledger",
            )
        )
        written.append(
            _fixed_source(
                _write_csv(execution.orders_df, output_dir / "portfolio_orders.csv"),
                catalog_key="execution_ledger",
            )
        )
        written.append(
            _fixed_source(
                _write_csv(execution.fills_df, output_dir / "portfolio_fills.csv"),
                catalog_key="execution_ledger",
            )
        )
    return tuple(written)


__all__ = ["write_execution_outputs"]

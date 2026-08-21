from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = WEB_ROOT / "strategy_library"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.result import (
    RUN_RESULT_SCHEMA_VERSION,
    CurvePayload,
    LedgerExecutionArtifacts,
    RunDiagnostics,
    RunIdentity,
    RunProvenance,
    RunRequestSummary,
    RunResult,
    VectorizedExecutionArtifacts,
    compute_curve_metrics,
)
from web.exporter import build_daily_holdings_payload
from web.cache_generation import publish_web_cache_generation, resolve_current_web_cache


@dataclass(slots=True)
class RebuildResult:
    name: str
    output_path: Path
    date_count: int
    holding_row_count: int


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False)
    tmp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _sorted_csv_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.csv"), key=lambda path: path.name.casefold())


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def _load_return_frames(strategy_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for csv_path in _sorted_csv_files(strategy_dir / "return_date_with_returns"):
        df = _load_csv(csv_path)
        if df.empty:
            continue
        frames[csv_path.stem] = df
    return frames


def _load_run_result(strategy_dir: Path) -> RunResult:
    merged_path = strategy_dir / "net_value_unique_date" / "merged_net_value_with_sum.csv"
    if not merged_path.is_file():
        raise FileNotFoundError(f"缺少净值文件: {merged_path}")

    merged_df = _load_csv(merged_path)
    if merged_df.empty:
        raise ValueError(f"净值文件为空: {merged_path}")

    return_frames = _load_return_frames(strategy_dir)
    fills_path = strategy_dir / "net_value_unique_date" / "portfolio_fills.csv"
    fills_df = _load_csv(fills_path) if fills_path.is_file() else pd.DataFrame()
    if not fills_df.empty:
        source = fills_df.get("source", pd.Series("strategy", index=fills_df.index)).astype(str).str.lower()
        risk_fills_df = fills_df.loc[source.eq("risk")].copy()
        strategy_fills_df = fills_df.loc[~source.eq("risk")].copy()
        execution = LedgerExecutionArtifacts(
            equity_curve=merged_df,
            orders_df=pd.DataFrame(),
            fills_df=strategy_fills_df,
            risk_orders_df=pd.DataFrame(),
            risk_fills_df=risk_fills_df,
            rejections_df=pd.DataFrame(),
            daily_state_df=merged_df,
            processed_frames=return_frames,
        )
        engine_id = "slot_broker"
    else:
        execution = VectorizedExecutionArtifacts(
            equity_curve=merged_df,
            trading_frames={},
            processed_frames=return_frames,
        )
        engine_id = "slot"
    return RunResult(
        schema_version=RUN_RESULT_SCHEMA_VERSION,
        identity=RunIdentity(run_id=f"rebuild:{strategy_dir.name}"),
        request=RunRequestSummary(
            mode="curve",
            engine_id=engine_id,
            start_time="",
            end_time="",
        ),
        payload=CurvePayload(
            engine_id=engine_id,
            decision_artifacts=None,
            equity_curve=merged_df,
            metrics=compute_curve_metrics(merged_df),
            execution=execution,
        ),
        diagnostics=RunDiagnostics(values={}),
        timings_sec={},
        provenance=RunProvenance(),
    )


def _load_initial_asset_hint(strategy_dir: Path) -> float | None:
    try:
        meta_path = resolve_current_web_cache(strategy_dir).artifact_path("meta.json")
    except Exception:
        return None
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    value = (
        meta.get("config", {})
        .get("portfolio", {})
        .get("initial_capital")
        if isinstance(meta.get("config"), dict)
        else None
    )
    try:
        amount = float(value)
    except Exception:
        return None
    return amount if amount > 0 else None


def rebuild_strategy_daily_holdings(name: str) -> RebuildResult:
    strategy_dir = LIBRARY_ROOT / name
    if not strategy_dir.is_dir():
        raise FileNotFoundError(f"策略目录不存在: {strategy_dir}")

    run_result = _load_run_result(strategy_dir)
    payload = build_daily_holdings_payload(
        run_result,
        initial_asset_hint=_load_initial_asset_hint(strategy_dir),
    )
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"无法重建每日持仓缓存: {name}")

    current = resolve_current_web_cache(strategy_dir)
    generation_id = (
        f"{current.generation_id}.holdings."
        f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    )
    with tempfile.TemporaryDirectory(prefix=".web-cache-holdings-", dir=str(strategy_dir)) as temp_dir:
        materialized = Path(temp_dir)
        for source_path in sorted(current.cache_dir.glob("*.json"), key=lambda path: path.name.casefold()):
            shutil.copy2(source_path, materialized / source_path.name)
        _write_json_atomic(materialized / "daily_holdings.json", payload)
        rebuilt = publish_web_cache_generation(strategy_dir, generation_id, materialized)
    output_path = rebuilt.artifact_path("daily_holdings.json")

    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    holdings_by_date = payload.get("holdings_by_date") if isinstance(payload.get("holdings_by_date"), dict) else {}
    holding_row_count = 0
    for rows in holdings_by_date.values():
        if isinstance(rows, list):
            holding_row_count += len(rows)

    return RebuildResult(
        name=name,
        output_path=output_path,
        date_count=len(dates),
        holding_row_count=holding_row_count,
    )


def _resolve_strategy_names(selected: list[str] | None) -> list[str]:
    if selected:
        return sorted({str(name).strip() for name in selected if str(name).strip()}, key=str.casefold)
    if not LIBRARY_ROOT.is_dir():
        return []
    return sorted(
        [path.name for path in LIBRARY_ROOT.iterdir() if path.is_dir()],
        key=str.casefold,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建 web 策略库 daily_holdings.json 缓存")
    parser.add_argument(
        "--name",
        dest="names",
        action="append",
        help="指定策略目录名；可重复传入多次。默认重建 strategy_library 下全部策略。",
    )
    args = parser.parse_args(argv)

    names = _resolve_strategy_names(args.names)
    if not names:
        print("未找到可重建的策略目录。", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    for name in names:
        try:
            result = rebuild_strategy_daily_holdings(name)
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"[FAILED] {name}: {exc}", file=sys.stderr)
            continue
        print(
            f"[OK] {result.name}: {result.output_path} "
            f"(dates={result.date_count}, holding_rows={result.holding_row_count})"
        )

    if failures:
        print("")
        print("重建失败列表:", file=sys.stderr)
        for name, reason in failures:
            print(f"- {name}: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

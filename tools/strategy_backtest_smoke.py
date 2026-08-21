"""Local-price smoke backtest for project strategy libraries.

The tool verifies that strategy modules can be loaded and run through the V1
portfolio backtest pipeline against a deterministic local price fixture. It
still requires configured TuShare calendar access; it is not a performance
benchmark and does not grade strategy returns.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT_ROOT = ROOT / "output" / "tools" / "strategy_backtest_smoke"
DEFAULT_STRATEGY_DIR = ROOT / "strategy_library"
DEFAULT_WEB_STRATEGY_DIR = ROOT / "web" / "strategy_library"
DEFAULT_STOCK_CODE = "300001.SZ"
DEFAULT_START = "20250102"
DEFAULT_END = "20250331"
SUCCESS_STATUSES = {"completed", "no_signal"}
INDEX_CODES = (
    "000001.SH",
    "000016.SH",
    "000300.SH",
    "000905.SH",
    "399001.SZ",
    "399006.SH",
    "399006.SZ",
)
BOARD_CODES = ("881001.TI", "881002.TI", "883300.TI", "885700.TI")
DAILY_FIELDS = ("amount", "net_mf_amount", "net_amount", "net_amount_rate", "pre_close", "pct_chg")


@dataclass(frozen=True, slots=True)
class StrategyTarget:
    name: str
    kind: str
    module: str
    path: str


@dataclass(frozen=True, slots=True)
class StrategySmokeResult:
    name: str
    kind: str
    module: str
    path: str
    status: str
    duration_sec: float
    final_net_value: float | None = None
    max_drawdown: float | None = None
    signal_stock_count: int = 0
    trade_count: int = 0
    error: str = ""
    traceback: str = ""


@dataclass(frozen=True, slots=True)
class SmokeFixture:
    sample_dir: Path
    stock_codes: tuple[str, ...]
    board_member_dict: dict[str, list[str]]


@contextmanager
def temporary_env(overrides: Mapping[str, object | None]) -> Iterator[None]:
    keys = tuple(sorted((str(key) for key in overrides), key=str.casefold))
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        value = overrides[key]
        if value is None or str(value).strip() == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    try:
        from config import get_config

        get_config(reload=True)
    except Exception:
        pass
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            from config import get_config

            get_config(reload=True)
        except Exception:
            pass


@contextmanager
def patched_board_member_source(member_dict: dict[str, list[str]]) -> Iterator[None]:
    import data.timing_readers as timing_readers

    old_reader = timing_readers.read_board_member_dict

    def _read_board_member_dict(_path: str | Path | None = None) -> dict[str, list[str]]:
        return {str(code): list(members) for code, members in member_dict.items()}

    timing_readers.read_board_member_dict = _read_board_member_dict
    try:
        yield
    finally:
        timing_readers.read_board_member_dict = old_reader


def _safe_slug(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "strategy"
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)


def _strategy_file_allowed(path: Path) -> bool:
    name = path.name
    stem = path.stem
    if path.suffix.lower() != ".py":
        return False
    if stem in {"__init__", "common"}:
        return False
    if stem.startswith("_") or name.startswith("__"):
        return False
    return True


def _path_to_module(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None
    if rel.suffix.lower() != ".py":
        return None
    return ".".join(rel.with_suffix("").parts)


def discover_strategy_library_targets(strategy_dirs: Iterable[Path]) -> list[StrategyTarget]:
    targets: list[StrategyTarget] = []
    for raw_dir in strategy_dirs:
        strategy_dir = Path(raw_dir)
        if not strategy_dir.is_dir():
            continue
        for path in sorted(strategy_dir.rglob("*.py"), key=lambda p: str(p.relative_to(strategy_dir)).casefold()):
            if not _strategy_file_allowed(path):
                continue
            module = _path_to_module(path)
            if module is None:
                continue
            targets.append(
                StrategyTarget(
                    name=module,
                    kind="strategy_library",
                    module=module,
                    path=str(path.resolve()),
                )
            )
    return sorted(targets, key=lambda item: item.name.casefold())


def discover_web_strategy_targets(web_dir: Path) -> list[StrategyTarget]:
    root = Path(web_dir)
    if not root.is_dir():
        return []
    targets: list[StrategyTarget] = []
    for path in sorted(root.rglob("strategy.py"), key=lambda p: str(p.relative_to(root)).casefold()):
        if not path.is_file():
            continue
        name = path.parent.name
        targets.append(
            StrategyTarget(
                name=f"web:{name}",
                kind="web",
                module=f"web.strategy_library.{name}.strategy",
                path=str(path.resolve()),
            )
        )
    return targets


def _target_matches(target: StrategyTarget, token: str) -> bool:
    text = str(token or "").strip()
    if not text:
        return False
    folded = text.casefold()
    fields = {
        target.name.casefold(),
        target.module.casefold(),
        Path(target.path).stem.casefold(),
        Path(target.path).name.casefold(),
        Path(target.path).parent.name.casefold(),
    }
    if folded in fields:
        return True
    return target.name.casefold().endswith(f".{folded}") or target.module.casefold().endswith(f".{folded}")


def select_targets(targets: Iterable[StrategyTarget], tokens: Iterable[str]) -> list[StrategyTarget]:
    ordered = list(targets)
    wanted_tokens = [str(token).strip() for token in tokens if str(token).strip()]
    if not wanted_tokens:
        return ordered

    selected: list[StrategyTarget] = []
    for token in wanted_tokens:
        path = Path(token)
        for target in ordered:
            if path.exists() and Path(target.path).resolve() == path.resolve():
                selected.append(target)
                continue
            if _target_matches(target, token):
                selected.append(target)

    deduped: dict[tuple[str, str], StrategyTarget] = {}
    for target in selected:
        deduped[(target.kind, target.name)] = target
    return sorted(deduped.values(), key=lambda item: item.name.casefold())


def _copy_base_fixture(sample_dir: Path) -> None:
    from app.offline_smoke_service import materialize_offline_smoke_data

    materialize_offline_smoke_data(sample_dir)


def _enrich_stock_frame(df: pd.DataFrame, *, stock_code: str) -> pd.DataFrame:
    out = df.copy()
    out["stock_code"] = stock_code
    out["stock_name"] = "OFFLINE_SMOKE"
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="raise")
    close = out["close"].astype(float)
    volume = out["volume"].astype(float)
    pre_close = close.shift(1).fillna(close.iloc[0])
    out["pre_close"] = pre_close
    out["pct_chg"] = (close / pre_close.replace(0.0, np.nan) - 1.0).fillna(0.0) * 100.0
    out["amount"] = (close * volume / 1000.0).round(4)
    idx = np.arange(len(out), dtype=float)
    net_mf_amount = 2200.0 + 900.0 * np.sin(idx / 3.0) + 500.0 * np.cos(idx / 7.0)
    out["net_mf_amount"] = np.round(net_mf_amount, 4)
    out["net_amount"] = out["net_mf_amount"]
    amount_wan = out["amount"].replace(0.0, np.nan) / 10.0
    out["net_amount_rate"] = (out["net_amount"] / amount_wan * 100.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def _build_market_frame(stock_df: pd.DataFrame, *, code: str, scale: float) -> pd.DataFrame:
    out = stock_df.loc[:, ["trade_date", "open", "high", "low", "close", "volume", "amount"]].copy()
    out["stock_code"] = code
    out["ts_code"] = code
    out["ths_index_code"] = code
    for col in ("open", "high", "low", "close"):
        out[col] = (pd.to_numeric(out[col], errors="raise") * scale).round(4)
    out["volume"] = (pd.to_numeric(out["volume"], errors="raise") * max(scale, 0.1)).round(0).astype(int)
    out["amount"] = (pd.to_numeric(out["amount"], errors="raise") * max(scale, 0.1)).round(4)
    return out


def prepare_smoke_fixture(
    sample_dir: Path,
    *,
    stock_code: str = DEFAULT_STOCK_CODE,
    index_codes: Iterable[str] = INDEX_CODES,
    board_codes: Iterable[str] = BOARD_CODES,
) -> SmokeFixture:
    _copy_base_fixture(sample_dir)
    prices_dir = sample_dir / "prices"
    stock_path = prices_dir / f"{DEFAULT_STOCK_CODE}.csv"
    stock_df = pd.read_csv(stock_path)
    stock_df = _enrich_stock_frame(stock_df, stock_code=stock_code)
    stock_df.to_csv(prices_dir / f"{stock_code}.csv", index=False)
    if stock_code != DEFAULT_STOCK_CODE and stock_path.exists():
        stock_path.unlink()

    pd.DataFrame({"stock_code": [stock_code], "stock_name": ["OFFLINE_SMOKE"]}).to_csv(
        sample_dir / "stock_basic.csv",
        index=False,
    )

    for i, code in enumerate(sorted(set(index_codes), key=str.casefold)):
        _build_market_frame(stock_df, code=code, scale=1.0 + i * 0.015).to_csv(prices_dir / f"{code}.csv", index=False)

    member_dict: dict[str, list[str]] = {}
    for i, code in enumerate(sorted(set(board_codes), key=str.casefold)):
        _build_market_frame(stock_df, code=code, scale=0.85 + i * 0.02).to_csv(prices_dir / f"{code}.csv", index=False)
        member_dict[code] = [stock_code]

    return SmokeFixture(sample_dir=sample_dir, stock_codes=(stock_code,), board_member_dict=member_dict)


def _load_strategy_target(target: StrategyTarget):
    from strategies.api import load_strategy_descriptor

    source = target.path if target.kind == "web" else target.module
    return load_strategy_descriptor(source)


def _build_env(
    sample_dir: Path,
    out_root: Path,
    *,
    target: StrategyTarget,
    start: str,
    end: str,
    stock_code: str,
) -> dict[str, str]:
    data_root = out_root / "_runs" / _safe_slug(target.name)
    return {
        "LOCAL_DATA_DIR": str(sample_dir.resolve()),
        "SAMPLE_DATA_DIR": str(sample_dir.resolve()),
        "STRATEGY_MODULE": target.module,
        "DATA_ROOT": str(data_root.resolve()),
        "SIGNAL_DATA_DIR": str((data_root / "signal_data").resolve()),
        "STOCK_DATA_DIR": str((data_root / "stock_data").resolve()),
        "TRADE_DATA_DIR": str((data_root / "trade_data").resolve()),
        "RETURN_DATE_DIR": str((data_root / "return_date").resolve()),
        "RETURN_DATE_WITH_RETURNS_DIR": str((data_root / "return_date_with_returns").resolve()),
        "NET_VALUE_UNIQUE_DATE_DIR": str((data_root / "net_value_unique_date").resolve()),
        "START_TIME": start,
        "END_TIME": end,
        "DEFAULT_STOCK_CODE": stock_code,
        "COMPUTE_METRICS": "1",
        "RUN_QUIET": "1",
        "PLOT_NET_VALUE": "0",
        "CLEAR_OUTPUTS": "0",
        "CLEAR_ALL_TABLES": "0",
        "DATA_MANIFEST_WRITE": "0",
        "STATE_MACHINE_JIT": "0",
        "NET_VALUE_JIT": "0",
        "PRICE_PANEL_SOURCE": "parquet",
        "PRICE_PANEL_PATH": str((sample_dir / "panel.parquet").resolve()),
        "PRICE_L2_CACHE": "0",
        "PRICE_L2_WRITE": "0",
        "PRICE_L2_MYSQL_MODE": "off",
        "OFFSET_DAYS": "0",
        "CHECK_TRADE_CALENDAR": "0",
        "PORTFOLIO_ENGINE": "legacy",
        "PYDANTIC_VALIDATE": "1",
        "PYDANTIC_VALIDATE_SAMPLE": "50",
        "MYSQL_DAILY_FIELDS": ",".join(DAILY_FIELDS),
    }


def _run_target_backtest(
    target: StrategyTarget,
    *,
    fixture: SmokeFixture,
    output_root: Path,
    start: str,
    end: str,
    quiet: bool,
) -> StrategySmokeResult:
    t0 = time.perf_counter()
    try:
        from backtest.orchestration import run_backtest
        from config import apply_config_overrides, get_config
        import config.app_config as app_config_module
        from core.execution_policy import get_execution_policy
        from runtime_lifecycle.reset import reset_runtime_state

        env = _build_env(
            fixture.sample_dir,
            output_root,
            target=target,
            start=start,
            end=end,
            stock_code=fixture.stock_codes[0],
        )
        with temporary_env(env), patched_board_member_source(fixture.board_member_dict):
            reset_runtime_state(clear_strategy_cache=True)
            cfg = apply_config_overrides(
                get_config(reload=True),
                {
                    "strategy.module": target.module,
                    "backtest.start_time": start,
                    "backtest.end_time": end,
                    "backtest.offset_days": 0,
                    "mysql.daily_fields": DAILY_FIELDS,
                    "panel.source": "parquet",
                    "panel.path": str((fixture.sample_dir / "panel.parquet").resolve()),
                    "run.compute_metrics": True,
                    "run.plot_net_value": False,
                    "run.clear_outputs": False,
                    "run.clear_all_tables": False,
                    "run.quiet": True,
                    "run.check_trade_calendar": False,
                    "run.pydantic_validate": True,
                    "run.pydantic_validate_sample": 50,
                    "performance.state_machine_jit": False,
                    "performance.net_value_jit": False,
                    "portfolio.engine": "legacy",
                },
            )
            with app_config_module.config_cache_scope(cfg):
                descriptor = _load_strategy_target(target)
                strategy = descriptor.strategy
                spec = descriptor.spec
                risk_cfg = descriptor.risk_control
                execution_policy = get_execution_policy(cfg)
                devnull_ctx = open(os.devnull, "w", encoding="utf-8") if quiet else None
                try:
                    if devnull_ctx is None:
                        result = run_backtest(
                            start,
                            end,
                            cfg=cfg,
                            spec=spec,
                            execution_policy=execution_policy,
                            risk_cfg=risk_cfg,
                            stock_code_list=fixture.stock_codes,
                            strategy=strategy,
                            strategy_descriptor=descriptor,
                        )
                    else:
                        with devnull_ctx as devnull, redirect_stdout(devnull), redirect_stderr(devnull):
                            result = run_backtest(
                                start,
                                end,
                                cfg=cfg,
                                spec=spec,
                                execution_policy=execution_policy,
                                risk_cfg=risk_cfg,
                                stock_code_list=fixture.stock_codes,
                                strategy=strategy,
                                strategy_descriptor=descriptor,
                            )
                finally:
                    reset_runtime_state(clear_strategy_cache=True)

        status = "completed" if result.final_net_value is not None else "no_signal"
        return StrategySmokeResult(
            name=target.name,
            kind=target.kind,
            module=target.module,
            path=target.path,
            status=status,
            duration_sec=time.perf_counter() - t0,
            final_net_value=None if result.final_net_value is None else float(result.final_net_value),
            max_drawdown=None if result.max_drawdown is None else float(result.max_drawdown),
            signal_stock_count=int(getattr(result, "signal_stock_count", 0) or 0),
            trade_count=int(getattr(result, "actual_segment_trade_count", 0) or 0),
        )
    except Exception as exc:
        return StrategySmokeResult(
            name=target.name,
            kind=target.kind,
            module=target.module,
            path=target.path,
            status="failed",
            duration_sec=time.perf_counter() - t0,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(limit=20),
        )


def run_strategy_smoke(
    targets: Iterable[StrategyTarget],
    *,
    output_root: Path,
    sample_dir: Path | None = None,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    stock_code: str = DEFAULT_STOCK_CODE,
    keep_sample: bool = False,
    quiet: bool = True,
) -> dict[str, object]:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    allocated_sample = sample_dir is None
    if sample_dir is None:
        sample_dir = output_root / "_work" / f"offline_fixture_{uuid4().hex}"
    sample_dir = Path(sample_dir)

    try:
        fixture = prepare_smoke_fixture(sample_dir, stock_code=stock_code)
        from app.offline_smoke_service import build_offline_smoke_panel
        build_offline_smoke_panel(sample_dir, sample_dir / "panel.parquet")
        results = [
            _run_target_backtest(
                target,
                fixture=fixture,
                output_root=output_root,
                start=start,
                end=end,
                quiet=quiet,
            )
            for target in targets
        ]
    finally:
        if allocated_sample and not keep_sample:
            shutil.rmtree(sample_dir, ignore_errors=True)

    counter = Counter(result.status for result in results)
    failed_count = sum(count for status, count in counter.items() if status not in SUCCESS_STATUSES)
    payload: dict[str, object] = {
        "ok": failed_count == 0,
        "start": start,
        "end": end,
        "stock_codes": [stock_code],
        "target_count": len(results),
        "summary": {
            "total": len(results),
            "completed": int(counter.get("completed", 0)),
            "no_signal": int(counter.get("no_signal", 0)),
            "failed": int(failed_count),
        },
        "results": [asdict(result) for result in results],
    }
    if keep_sample:
        payload["sample_dir"] = str(sample_dir.resolve())
    return payload


def write_reports(payload: Mapping[str, object], *, json_out: Path, csv_out: Path) -> None:
    json_out = Path(json_out)
    csv_out = Path(csv_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    results = list(payload.get("results") or [])
    fieldnames = [
        "name",
        "kind",
        "module",
        "path",
        "status",
        "duration_sec",
        "final_net_value",
        "max_drawdown",
        "signal_stock_count",
        "trade_count",
        "error",
    ]
    with csv_out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            if isinstance(item, Mapping):
                writer.writerow({name: item.get(name, "") for name in fieldnames})


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local-price smoke backtests for strategy library modules (requires TuShare calendar access)."
    )
    parser.add_argument("targets", nargs="*", help="Optional strategy names, module suffixes, or file paths.")
    parser.add_argument(
        "--dir",
        dest="strategy_dirs",
        action="append",
        default=[],
        help="Strategy directory to scan. Can be repeated. Default: strategy_library.",
    )
    parser.add_argument("--web", dest="include_web", action="store_true", default=True, help="Include web strategy library.")
    parser.add_argument("--no-web", dest="include_web", action="store_false", help="Skip web strategy library.")
    parser.add_argument("--start", default=DEFAULT_START, help="Backtest start date YYYYMMDD.")
    parser.add_argument("--end", default=DEFAULT_END, help="Backtest end date YYYYMMDD.")
    parser.add_argument("--stock-code", default=DEFAULT_STOCK_CODE, help="Synthetic fixture stock code.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of selected targets, for quick diagnostics.")
    parser.add_argument("--keep-sample", action="store_true", help="Keep generated local fixture under output _work.")
    parser.add_argument("--no-quiet", action="store_true", help="Do not suppress per-strategy stdout/stderr.")
    parser.add_argument("--json-out", default="", help="JSON report path.")
    parser.add_argument("--csv-out", default="", help="CSV report path.")
    parser.add_argument("--output-root", default="", help="Output root for reports and temporary run data.")
    return parser


def _resolve_strategy_dirs(raw_dirs: Iterable[str]) -> list[Path]:
    dirs = [Path(item) for item in raw_dirs if str(item).strip()]
    return dirs if dirs else [DEFAULT_STRATEGY_DIR]


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_root = Path(args.output_root) if args.output_root else DEFAULT_OUTPUT_ROOT
    json_out = Path(args.json_out) if args.json_out else output_root / "strategy_backtest_smoke.json"
    csv_out = Path(args.csv_out) if args.csv_out else output_root / "strategy_backtest_smoke.csv"

    targets = discover_strategy_library_targets(_resolve_strategy_dirs(args.strategy_dirs))
    if bool(args.include_web):
        targets.extend(discover_web_strategy_targets(DEFAULT_WEB_STRATEGY_DIR))
    targets = sorted(targets, key=lambda item: item.name.casefold())
    targets = select_targets(targets, args.targets)
    if args.limit and int(args.limit) > 0:
        targets = targets[: int(args.limit)]

    payload = run_strategy_smoke(
        targets,
        output_root=output_root,
        start=str(args.start),
        end=str(args.end),
        stock_code=str(args.stock_code),
        keep_sample=bool(args.keep_sample),
        quiet=not bool(args.no_quiet),
    )
    write_reports(payload, json_out=json_out, csv_out=csv_out)
    summary = payload["summary"]
    print(json.dumps({"ok": payload["ok"], **summary}, ensure_ascii=True, sort_keys=True))
    return 0 if bool(payload["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

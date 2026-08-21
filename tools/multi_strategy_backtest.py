"""用途:
- 以独立子进程顺序回测多个策略模块，并把结果增量写入 `summary.csv` / `checkpoint.json`。
- 默认扫描 `strategy_library/generated`；每个策略有独立 wall-clock timeout，超时后清理进程树并继续。

用法:
- 回测默认目录：`python tools/multi_strategy_backtest.py`
- 指定目录并保留中间产物：`python tools/multi_strategy_backtest.py --dir strategy_library/custom --keep-artifacts`
- 指定输出目录：`python tools/multi_strategy_backtest.py --output-dir output/tools/multi_strategy/manual`
- 指定单策略超时：`python tools/multi_strategy_backtest.py --timeout-sec 900`"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import time
import warnings
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, List, Optional, TextIO

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
# 将仓库根目录加入 sys.path，确保脚本直接运行时可以找到 config 等模块
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 批量脚本默认只保留最终汇总 CSV，关闭中间落盘和控制台噪音
os.environ["CLEAR_ALL_TABLES"] = "0"
os.environ["CLEAR_OUTPUTS"] = "0"
os.environ["PLOT_NET_VALUE"] = "0"
os.environ["RUN_QUIET"] = "1"
os.environ["DATA_MANIFEST_WRITE"] = "0"

import config.app_config as app_config_module
from config import get_config
from backtest.engines import ENGINE_REGISTRY
from core.config_validator import validate_config
from core.execution_policy import get_execution_policy
from core.logging import close_run_logger_safely, get_run_logger
from runtime_lifecycle.reset import reset_runtime_state
from core.run_metadata import _git_snapshot, build_run_metadata, create_run_id, write_run_metadata
from backtest.orchestration import run_backtest
from backtest.result import CurvePayload
from backtest.artifacts import ArtifactPolicy
from strategies.api import load_strategy_descriptor


# ===== 用户配置区域 =====
# 修改下面的目录路径来指定批量回测的策略目录
BACKTEST_STRATEGY_DIR = ROOT / "strategy_library" / "generated"
# 其他常用目录：
# BACKTEST_STRATEGY_DIR = ROOT / "strategy_library" / "custom"
# ===== 配置区域结束 =====

DEFAULT_BASE_OUTPUT = ROOT / "output" / "tools" / "multi_strategy"
SUMMARY_FIELDNAMES = [
    "module",
    "status",
    "duration_sec",
    "engine",
    "transaction_cost_enabled",
    "cum_return",
    "max_drawdown",
    "final_net_value",
    "trade_count",
    "win_rate",
    "git_commit",
    "git_dirty",
    "run_id",
    "run_meta_path",
    "output_dir",
    "error",
    "timed_out",
    "exit_code",
    "checkpoint_path",
]


@dataclass
class StrategyResult:
    module: str
    status: str
    duration: float
    output_dir: Path
    engine: Optional[str] = None
    transaction_cost_enabled: Optional[bool] = None
    cum_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    final_net_value: Optional[float] = None
    trade_count: Optional[int] = None
    win_rate: Optional[float] = None
    git_commit: Optional[str] = None
    git_dirty: Optional[bool] = None
    run_id: Optional[str] = None
    run_meta_path: Optional[Path] = None
    error: Optional[str] = None
    timed_out: bool = False
    exit_code: Optional[int] = None
    checkpoint_path: Optional[Path] = None


@dataclass(frozen=True)
class BatchRuntimeOptions:
    write_run_metadata: bool
    write_running_metadata: bool
    write_logs: bool
    git_snapshot: dict[str, Any] | None
    artifact_policy: str = ArtifactPolicy.NONE.value


@dataclass(frozen=True)
class BatchCliOptions:
    output_dir: Path
    keep_artifacts: bool
    strategy_dir: Path | None
    targets: tuple[str, ...]
    timeout_sec: float


class _NullLogger:
    __slots__ = ()

    handlers: tuple[()] = ()
    name = "null"
    propagate = False

    @staticmethod
    def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    debug = _noop
    info = _noop
    warning = _noop
    error = _noop
    exception = _noop
    critical = _noop

    def __getattr__(self, _name: str):
        return self._noop


_NULL_LOGGER = _NullLogger()


_CLEANABLE_OUTPUT_NAMES = {
    "summary.csv",
    "checkpoint.json",
    "_child_results",
    "_work",
    "artifacts",
    "run_meta",
    "logs",
}
_REPO_RELATIVE_PATH_PREFIXES = {
    "config",
    "data",
    "docs",
    "output",
    "projects",
    "strategies",
    "strategy_library",
    "tests",
    "tools",
}


@contextmanager
def _config_cache_scope(cfg) -> Iterable[None]:
    with app_config_module.config_cache_scope(cfg):
        yield


class SummaryCsvWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "summary.csv"
        self._handle: TextIO | None = None
        self._writer = None

    def __enter__(self) -> "SummaryCsvWriter":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._handle, fieldnames=SUMMARY_FIELDNAMES)
        self._writer.writeheader()
        self._handle.flush()
        return self

    def write(self, result: StrategyResult) -> None:
        if self._handle is None or self._writer is None:
            raise RuntimeError("summary writer is not open")
        self._writer.writerow(_result_to_summary_row(result))
        self._handle.flush()

    def write_many(self, results: Iterable[StrategyResult]) -> None:
        for result in results:
            self.write(result)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle = None
        self._writer = None


def _result_to_summary_row(res: StrategyResult) -> dict[str, object]:
    return {
        "module": res.module,
        "status": res.status,
        "duration_sec": round(res.duration, 6),
        "engine": "" if not res.engine else res.engine,
        "transaction_cost_enabled": "" if res.transaction_cost_enabled is None else int(bool(res.transaction_cost_enabled)),
        "cum_return": "" if res.cum_return is None else res.cum_return,
        "max_drawdown": "" if res.max_drawdown is None else res.max_drawdown,
        "final_net_value": "" if res.final_net_value is None else res.final_net_value,
        "trade_count": "" if res.trade_count is None else res.trade_count,
        "win_rate": "" if res.win_rate is None else res.win_rate,
        "git_commit": "" if not res.git_commit else res.git_commit,
        "git_dirty": "" if res.git_dirty is None else int(bool(res.git_dirty)),
        "run_id": "" if not res.run_id else res.run_id,
        "run_meta_path": "" if res.run_meta_path is None else str(res.run_meta_path),
        "output_dir": str(res.output_dir),
        "error": "" if not res.error else res.error,
        "timed_out": int(bool(res.timed_out)),
        "exit_code": "" if res.exit_code is None else int(res.exit_code),
        "checkpoint_path": "" if res.checkpoint_path is None else str(res.checkpoint_path),
    }


def _result_to_payload(res: StrategyResult) -> dict[str, object]:
    """Serialize a child result without leaking non-JSON Path objects."""
    return {
        "module": res.module,
        "status": res.status,
        "duration": float(res.duration),
        "output_dir": str(res.output_dir),
        "engine": res.engine,
        "transaction_cost_enabled": res.transaction_cost_enabled,
        "cum_return": res.cum_return,
        "max_drawdown": res.max_drawdown,
        "final_net_value": res.final_net_value,
        "trade_count": res.trade_count,
        "win_rate": res.win_rate,
        "git_commit": res.git_commit,
        "git_dirty": res.git_dirty,
        "run_id": res.run_id,
        "run_meta_path": None if res.run_meta_path is None else str(res.run_meta_path),
        "error": res.error,
        "timed_out": bool(res.timed_out),
        "exit_code": res.exit_code,
        "checkpoint_path": None if res.checkpoint_path is None else str(res.checkpoint_path),
    }


def _result_from_payload(payload: object, *, module: str, fallback_output: Path) -> StrategyResult:
    if not isinstance(payload, dict):
        raise ValueError("child result payload must be an object")
    return StrategyResult(
        module=str(payload.get("module") or module),
        status=str(payload.get("status") or "failed"),
        duration=float(payload.get("duration") or 0.0),
        output_dir=Path(str(payload.get("output_dir") or fallback_output)),
        engine=str(payload["engine"]) if payload.get("engine") not in (None, "") else None,
        transaction_cost_enabled=(
            bool(payload["transaction_cost_enabled"])
            if payload.get("transaction_cost_enabled") is not None
            else None
        ),
        cum_return=float(payload["cum_return"]) if payload.get("cum_return") is not None else None,
        max_drawdown=float(payload["max_drawdown"]) if payload.get("max_drawdown") is not None else None,
        final_net_value=(
            float(payload["final_net_value"]) if payload.get("final_net_value") is not None else None
        ),
        trade_count=int(payload["trade_count"]) if payload.get("trade_count") is not None else None,
        win_rate=float(payload["win_rate"]) if payload.get("win_rate") is not None else None,
        git_commit=str(payload["git_commit"]) if payload.get("git_commit") not in (None, "") else None,
        git_dirty=bool(payload["git_dirty"]) if payload.get("git_dirty") is not None else None,
        run_id=str(payload["run_id"]) if payload.get("run_id") not in (None, "") else None,
        run_meta_path=Path(str(payload["run_meta_path"])) if payload.get("run_meta_path") else None,
        error=str(payload["error"]) if payload.get("error") not in (None, "") else None,
        timed_out=bool(payload.get("timed_out", False)),
        exit_code=int(payload["exit_code"]) if payload.get("exit_code") is not None else None,
        checkpoint_path=Path(str(payload["checkpoint_path"])) if payload.get("checkpoint_path") else None,
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_batch_checkpoint(
    path: Path,
    *,
    targets: Iterable[str],
    results: Iterable[StrategyResult],
    status: str,
    timeout_sec: float,
) -> None:
    ordered_results = list(results)
    _write_json_atomic(
        path,
        {
            "schema_version": 1,
            "status": str(status),
            "timeout_sec": float(timeout_sec),
            "targets": sorted((str(item) for item in targets), key=str.casefold),
            "completed_count": len(ordered_results),
            "results": [_result_to_payload(item) for item in ordered_results],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _should_collect_batch_git_snapshot() -> bool:
    raw = str(os.environ.get("RUN_META_LEVEL") or "").strip().lower()
    default = raw not in {"min", "minimal", "lite"}
    return _bool_env("RUN_META_GIT", default)


def _build_batch_runtime_options(cfg, *, keep_artifacts: bool) -> BatchRuntimeOptions:
    write_run_metadata = bool(keep_artifacts)
    write_logs = bool(keep_artifacts)
    write_running_metadata = write_run_metadata and _bool_env("MULTI_STRATEGY_WRITE_RUNNING_META", False)
    git_snapshot = None
    if _should_collect_batch_git_snapshot():
        git_snapshot = _git_snapshot(Path(cfg.paths.repo_root))
    default_artifact_policy = (
        ArtifactPolicy.FULL.value if keep_artifacts else ArtifactPolicy.NONE.value
    )
    artifact_policy = str(
        os.environ.get("MULTI_STRATEGY_ARTIFACT_POLICY", default_artifact_policy)
    ).strip().casefold()
    ArtifactPolicy.coerce(artifact_policy)
    return BatchRuntimeOptions(
        write_run_metadata=write_run_metadata,
        write_running_metadata=write_running_metadata,
        write_logs=write_logs,
        git_snapshot=git_snapshot,
        artifact_policy=artifact_policy,
    )


def _looks_repo_relative(path: Path) -> bool:
    return bool(path.parts) and path.parts[0].casefold() in _REPO_RELATIVE_PATH_PREFIXES


def _resolve_cli_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate
    repo_candidate = ROOT / candidate
    if repo_candidate.exists() or _looks_repo_relative(candidate):
        return repo_candidate
    return candidate


@contextmanager
def _suppress_strategy_module_warnings(module: str | None):
    if not _bool_env("MULTI_STRATEGY_SUPPRESS_STRATEGY_WARNINGS", True):
        yield
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _discover_strategy_files(strategy_dir: Path) -> List[Path]:
    files: List[Path] = []
    if not strategy_dir.is_dir():
        return files
    for path in sorted(strategy_dir.glob("*.py"), key=lambda p: p.name.casefold()):
        stem = path.stem
        if stem in {"__init__", "common"} or stem.startswith("_"):
            continue
        files.append(path)
    return files


def discover_builtin_modules() -> List[str]:
    builtin_dir = BACKTEST_STRATEGY_DIR
    modules: List[str] = []
    for path in _discover_strategy_files(builtin_dir):
        module = _path_to_module(path)
        if module is not None:
            modules.append(module)
    return modules


def _module_slug(module: str) -> str:
    p = Path(module)
    if p.suffix and p.exists():
        return p.stem
    return module.split(".")[-1]


def _path_to_module(path: Path) -> str | None:
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(ROOT.resolve())
    except Exception:
        return None
    if rel.suffix.lower() != ".py":
        return None
    return ".".join(rel.with_suffix("").parts)


def _normalize_strategy_target(raw: str | Path) -> str:
    path = Path(raw)
    if path.suffix.lower() == ".py":
        path = _resolve_cli_path(path)
        module = _path_to_module(path)
        if module:
            return module
    return str(raw)


def _module_matches_token(module: str, token: str) -> bool:
    token = str(token).strip()
    if not token:
        return False
    module_cf = module.casefold()
    token_cf = token.casefold()
    if module_cf == token_cf:
        return True
    stem = module.rsplit(".", 1)[-1]
    stem_cf = stem.casefold()
    if token_cf in {stem_cf, f"{stem_cf}.py"}:
        return True
    return "." in token and module_cf.endswith(f".{token_cf}")


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _output_dir_has_multi_strategy_hint(path: Path) -> bool:
    return any("multi_strategy" in part.casefold() for part in path.parts)


def _validate_batch_output_dir(output_dir: Path) -> Path:
    resolved = Path(output_dir).expanduser().resolve()
    root = ROOT.resolve()
    root_output = (ROOT / "output").resolve()
    cwd = Path.cwd().resolve()
    dangerous = {
        root,
        root / "tools",
        cwd,
        Path.home().resolve(),
    }
    if resolved.parent == resolved or resolved in dangerous:
        raise ValueError(f"拒绝清理危险输出目录: {resolved}")
    if not resolved.exists():
        return resolved
    if not resolved.is_dir():
        raise ValueError(f"输出路径不是目录: {resolved}")
    if not any(resolved.iterdir()):
        return resolved
    if _is_relative_to(resolved, root_output) or _output_dir_has_multi_strategy_hint(resolved):
        return resolved
    raise ValueError(
        f"拒绝清理疑似非批量回测输出目录: {resolved}。"
        "请使用 output/tools/multi_strategy 下的目录，或目录名包含 multi_strategy。"
    )


def _cleanup_output_root(output_dir: Path, *, keep_names: Iterable[str] = ()) -> None:
    _validate_batch_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    keep = {str(name).casefold() for name in keep_names}
    for child_name in sorted(os.listdir(output_dir), key=str.casefold):
        if child_name.casefold() in keep:
            continue
        if child_name.casefold() not in _CLEANABLE_OUTPUT_NAMES:
            continue
        _remove_path(output_dir / child_name)


def _default_work_output(output_dir: Path, *, keep_artifacts: bool) -> Path:
    return output_dir / ("artifacts" if keep_artifacts else "_work")


def _metadata_output_root(base_output: Path, *, cleanup_output: bool) -> Path:
    if not cleanup_output:
        return base_output
    if base_output.name.casefold() == "_work":
        return base_output.parent
    return base_output


def _batch_keep_names(*, keep_artifacts: bool) -> set[str]:
    names = {"summary.csv", "checkpoint.json"}
    if keep_artifacts:
        names.update({"artifacts", "run_meta", "logs"})
    return names


def prepare_env(module: str, base_output: Path) -> Path:
    slug = _module_slug(module)
    out_root = base_output / slug
    env_map = {
        "STRATEGY_MODULE": module,
        "DATA_ROOT": str(out_root),
        "SIGNAL_DATA_DIR": str(out_root / "signal_data"),
        "STOCK_DATA_DIR": str(out_root / "stock_data"),
        "TRADE_DATA_DIR": str(out_root / "trade_data"),
        "RETURN_DATE_DIR": str(out_root / "return_date"),
        "RETURN_DATE_WITH_RETURNS_DIR": str(out_root / "return_date_with_returns"),
        "NET_VALUE_UNIQUE_DATE_DIR": str(out_root / "net_value_unique_date"),
        "CLEAR_OUTPUTS": "0",
        "CLEAR_ALL_TABLES": "0",
        "PLOT_NET_VALUE": "0",
        "RUN_QUIET": "1",
        "DATA_MANIFEST_WRITE": "0",
    }
    for k, v in env_map.items():
        os.environ[k] = str(v)
    os.environ.pop("LOCAL_DATA_DIR", None)
    os.environ.pop("SAMPLE_DATA_DIR", None)
    out_root.mkdir(parents=True, exist_ok=True)
    return out_root


def _build_strategy_cfg(base_cfg, *, module: str, out_root: Path):
    return replace(
        base_cfg,
        strategy=replace(base_cfg.strategy, module=module),
        paths=replace(
            base_cfg.paths,
            data_root=out_root,
            signal_data=out_root / "signal_data",
            stock_data=out_root / "stock_data",
            trade_data=out_root / "trade_data",
            return_date=out_root / "return_date",
            return_date_with_returns=out_root / "return_date_with_returns",
            net_value_unique_date=out_root / "net_value_unique_date",
        ),
    )


def _select_targets(argv: Iterable[str], discovered: List[str]) -> List[str]:
    if not argv:
        return discovered
    wanted = []
    missing = []
    for raw in argv:
        token = raw.strip()
        if not token:
            continue
        matches = [module for module in discovered if _module_matches_token(module, token)]
        if matches:
            wanted.extend(matches)
        else:
            missing.append(token)
    wanted = sorted(dict.fromkeys(wanted), key=str.casefold)
    if missing:
        print(f"[WARN] 未找到策略: {', '.join(missing)}")
    return wanted


def _select_targets_in_dir(argv: Iterable[str], strategy_dir: Path, discovered: List[Path]) -> List[str]:
    if not argv:
        return [_normalize_strategy_target(p) for p in discovered]
    wanted: List[str] = []
    for raw in argv:
        token = raw.strip()
        if not token:
            continue
        p = _resolve_cli_path(token)
        if p.exists() and p.is_file():
            wanted.append(_normalize_strategy_target(p))
            continue
        match = None
        for cand in discovered:
            if (
                cand.name == token
                or cand.stem == token
                or cand.name.casefold() == token.casefold()
                or cand.stem.casefold() == token.casefold()
            ):
                match = cand
                break
        if match is not None:
            wanted.append(_normalize_strategy_target(match))
            continue
        if not token.lower().endswith(".py"):
            cand = strategy_dir / f"{token}.py"
            if cand.exists():
                wanted.append(_normalize_strategy_target(cand))
                continue
        wanted.append(token)
    wanted = sorted(dict.fromkeys(wanted), key=str.casefold)
    available = {_normalize_strategy_target(p) for p in discovered}
    missing = [m for m in wanted if m not in available]
    if missing:
        print(f"[WARN] 未找到策略: {', '.join(missing)}")
    return [m for m in wanted if m in available]


def run_one(
    module: str,
    *,
    base_cfg=None,
    base_execution_policy=None,
    stock_code_list: tuple[str, ...] | None = None,
    quiet_console: bool = False,
    base_output: Path | None = None,
    cleanup_output: bool = True,
    runtime_options: BatchRuntimeOptions | None = None,
) -> StrategyResult:
    _ = quiet_console
    t0 = time.time()
    base_output = Path(base_output or _default_work_output(DEFAULT_BASE_OUTPUT, keep_artifacts=False))
    out_root = prepare_env(module, base_output)
    runtime_options = runtime_options or BatchRuntimeOptions(
        write_run_metadata=not cleanup_output,
        write_running_metadata=False,
        write_logs=not cleanup_output,
        git_snapshot=None,
    )
    stable_output = _metadata_output_root(base_output, cleanup_output=cleanup_output)

    cfg = None
    policy = None
    meta = None
    run_id = create_run_id()
    run_meta_path: Path | None = None
    run_meta_dir = stable_output / "run_meta"
    git_snapshot = runtime_options.git_snapshot
    owned_logger = None

    def _write_metadata(
        *,
        strategy_meta=None,
        risk_cfg=None,
        extra: dict[str, Any] | None = None,
        result_payload: dict[str, Any] | None = None,
        duration_sec: float | None = None,
    ) -> dict[str, Any] | None:
        nonlocal run_meta_path
        if not runtime_options.write_run_metadata or cfg is None:
            return None
        payload = build_run_metadata(
            cfg,
            run_id=run_id,
            start_time=cfg.backtest.start_time,
            end_time=cfg.backtest.end_time,
            strategy_module=module,
            strategy_meta=strategy_meta,
            execution_policy=policy,
            risk_cfg=risk_cfg,
            extra=extra,
            git_snapshot=git_snapshot,
        )
        if result_payload is not None:
            payload["result"] = result_payload
        if duration_sec is not None:
            payload["duration_sec"] = float(duration_sec)
        run_meta_path = write_run_metadata(run_meta_dir, payload, filename=f"{run_id}.json")
        return payload

    def _strategy_result(
        *,
        status: str,
        duration: float,
        error: str | None = None,
        cum_return: float | None = None,
        max_drawdown: float | None = None,
        final_net_value: float | None = None,
        trade_count: int | None = None,
        win_rate: float | None = None,
    ) -> StrategyResult:
        git_info = (meta.get("git") if isinstance(meta, dict) else None) or git_snapshot or {}
        git_commit = str(git_info.get("commit") or "") or None
        return StrategyResult(
            module=module,
            status=status,
            duration=duration,
            output_dir=out_root,
            engine=str(getattr(cfg.portfolio, "engine", "") or "") if cfg is not None else None,
            transaction_cost_enabled=bool(getattr(cfg.transaction_cost, "enable", False)) if cfg is not None else None,
            cum_return=cum_return,
            max_drawdown=max_drawdown,
            final_net_value=final_net_value,
            trade_count=trade_count,
            win_rate=win_rate,
            git_commit=git_commit,
            git_dirty=git_info.get("dirty"),
            run_id=run_id,
            run_meta_path=run_meta_path,
            error=error,
        )

    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull, redirect_stdout(devnull):
            if base_cfg is None:
                cfg = get_config(reload=True)
                try:
                    policy = get_execution_policy(cfg)
                except Exception:
                    policy = None

                errors, _warnings = validate_config(cfg, engine_catalog=ENGINE_REGISTRY)
                if errors:
                    meta = _write_metadata(
                        extra={"status": "config_failed", "reason": "config_validation"},
                    )
                    return _strategy_result(
                        status="config_failed",
                        duration=time.time() - t0,
                        error="; ".join(errors),
                    )
            else:
                cfg = _build_strategy_cfg(base_cfg, module=module, out_root=out_root)
                policy = base_execution_policy if base_execution_policy is not None else get_execution_policy(cfg)

            reset_runtime_state(clear_strategy_cache=True)
            with _config_cache_scope(cfg):
                logger = _NULL_LOGGER
                if runtime_options.write_logs:
                    owned_logger = get_run_logger(run_id, stable_output / "logs")
                    logger = owned_logger

                with _suppress_strategy_module_warnings(module):
                    try:
                        descriptor = load_strategy_descriptor(module)
                        strategy = descriptor.strategy
                    except Exception as e:
                        meta = _write_metadata(
                            extra={"status": "load_failed", "reason": f"load_strategy_descriptor: {e}"},
                        )
                        return _strategy_result(
                            status="load_failed",
                            duration=time.time() - t0,
                            error=str(e),
                        )

                    spec = descriptor.spec
                    risk_cfg = descriptor.risk_control
                    if runtime_options.write_running_metadata:
                        meta = _write_metadata(
                            strategy_meta=spec,
                            risk_cfg=risk_cfg,
                            extra={"status": "running"},
                        )

                    try:
                        result = run_backtest(
                            cfg.backtest.start_time,
                            cfg.backtest.end_time,
                            cfg=cfg,
                            spec=spec,
                            execution_policy=policy,
                            risk_cfg=risk_cfg,
                            artifact_policy=runtime_options.artifact_policy,
                            stock_code_list=stock_code_list,
                            strategy=strategy,
                            strategy_descriptor=descriptor,
                            logger=logger,
                            run_id=run_id,
                        )
                    except Exception as e:
                        meta = _write_metadata(
                            strategy_meta=spec,
                            risk_cfg=risk_cfg,
                            extra={"status": "failed", "reason": f"run_backtest: {e}"},
                            duration_sec=time.time() - t0,
                        )
                        return _strategy_result(
                            status="failed",
                            duration=time.time() - t0,
                            error=str(e),
                        )

            payload = result.payload
            metrics = payload.metrics if isinstance(payload, CurvePayload) else None
            final_net_value = metrics.final_net_value if metrics is not None else None
            cum_return = None if final_net_value is None else float(final_net_value - 1.0)
            max_dd = metrics.max_drawdown if metrics is not None else None
            trade_count = int(metrics.actual_segment_trade_count) if metrics is not None else 0
            win_rate = metrics.actual_segment_win_rate if metrics is not None and trade_count > 0 else None
            status = "completed" if cum_return is not None else "no_signal"
            result_payload = {
                "final_net_value": final_net_value,
                "cum_return": cum_return,
                "max_drawdown": max_dd,
                "signal_stock_count": int(result.request.signal_stock_count),
                "actual_segment_trade_count": trade_count,
                "actual_segment_avg_trade_return": (
                    float(metrics.actual_segment_avg_trade_return) if metrics is not None else 0.0
                ),
                "actual_segment_win_rate": (
                    float(metrics.actual_segment_win_rate) if metrics is not None else 0.0
                ),
            }
            meta = _write_metadata(
                strategy_meta=spec,
                risk_cfg=risk_cfg,
                extra={"status": status},
                result_payload=result_payload,
                duration_sec=time.time() - t0,
            )

            return _strategy_result(
                status=status,
                duration=time.time() - t0,
                cum_return=cum_return,
                max_drawdown=max_dd,
                final_net_value=final_net_value,
                trade_count=trade_count,
                win_rate=win_rate,
            )
    finally:
        try:
            close_run_logger_safely(owned_logger)
        finally:
            if cleanup_output:
                _remove_path(out_root)


_ACTIVE_CHILD_PROCESSES: set[object] = set()


def _child_creation_kwargs() -> dict[str, object]:
    if os.name == "nt":
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"creationflags": flags | no_window}
    return {"start_new_session": True}


def _terminate_process_tree(
    process: object,
    *,
    grace_sec: float = 5.0,
    force_tree: bool = False,
) -> None:
    """Terminate a child and every descendant it owns."""
    pid = int(getattr(process, "pid", 0) or 0)
    if pid <= 0:
        return
    poll = getattr(process, "poll", None)
    if callable(poll) and poll() is not None and not force_tree:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=max(0.1, float(grace_sec)))
            return
        except Exception:
            pass
    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except OSError:
            pass
    if callable(wait):
        try:
            wait(timeout=max(0.1, float(grace_sec)))
        except Exception:
            pass


def _terminate_active_processes() -> None:
    for process in tuple(_ACTIVE_CHILD_PROCESSES):
        _terminate_process_tree(process)
    _ACTIVE_CHILD_PROCESSES.clear()


def _child_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--child-run", required=True)
    parser.add_argument("--child-result", required=True)
    parser.add_argument("--child-output", required=True)
    parser.add_argument("--child-keep-artifacts", action="store_true")
    parser.add_argument("--child-artifact-policy", default=ArtifactPolicy.NONE.value)
    return parser


def _run_child_cli(argv: list[str]) -> int:
    ns = _child_cli_parser().parse_args(argv)
    result_path = Path(ns.child_result).expanduser().resolve()
    output_root = Path(ns.child_output).expanduser().resolve()
    artifact_policy = ArtifactPolicy.coerce(ns.child_artifact_policy).value
    options = BatchRuntimeOptions(
        write_run_metadata=bool(ns.child_keep_artifacts),
        write_running_metadata=bool(ns.child_keep_artifacts)
        and _bool_env("MULTI_STRATEGY_WRITE_RUNNING_META", False),
        write_logs=bool(ns.child_keep_artifacts),
        git_snapshot=None,
        artifact_policy=artifact_policy,
    )
    try:
        result = run_one(
            ns.child_run,
            base_output=output_root,
            cleanup_output=not bool(ns.child_keep_artifacts),
            runtime_options=options,
        )
    except Exception as exc:
        result = StrategyResult(
            module=str(ns.child_run),
            status="failed",
            duration=0.0,
            output_dir=output_root / _module_slug(ns.child_run),
            error=str(exc),
        )
    _write_json_atomic(result_path, _result_to_payload(result))
    return 0 if result.status in {"completed", "no_signal"} else 1


def _timeout_result(
    module: str,
    *,
    output_root: Path,
    timeout_sec: float,
    duration: float,
    exit_code: int | None,
    checkpoint_path: Path,
) -> StrategyResult:
    error = json.dumps(
        {
            "type": "wall_clock_timeout",
            "timeout_sec": float(timeout_sec),
            "module": module,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return StrategyResult(
        module=module,
        status="timeout",
        duration=duration,
        output_dir=output_root / _module_slug(module),
        error=error,
        timed_out=True,
        exit_code=exit_code,
        checkpoint_path=checkpoint_path,
    )


def _run_strategy_subprocess(
    module: str,
    *,
    output_root: Path,
    keep_artifacts: bool,
    artifact_policy: str,
    timeout_sec: float,
    result_dir: Path,
) -> StrategyResult:
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / f"{_module_slug(module)}.json"
    result_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child-run",
        module,
        "--child-result",
        str(result_path),
        "--child-output",
        str(output_root),
        "--child-artifact-policy",
        str(artifact_policy),
    ]
    if keep_artifacts:
        command.append("--child-keep-artifacts")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_child_creation_kwargs(),
    )
    _ACTIVE_CHILD_PROCESSES.add(process)
    started = time.monotonic()
    try:
        try:
            return_code = process.wait(timeout=(float(timeout_sec) if timeout_sec > 0 else None))
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process, force_tree=True)
            return _timeout_result(
                module,
                output_root=output_root,
                timeout_sec=timeout_sec,
                duration=time.monotonic() - started,
                exit_code=getattr(process, "returncode", None),
                checkpoint_path=result_path,
            )
        except KeyboardInterrupt:
            _terminate_process_tree(process, force_tree=True)
            raise
    finally:
        _ACTIVE_CHILD_PROCESSES.discard(process)

    if result_path.is_file():
        try:
            result = _result_from_payload(
                json.loads(result_path.read_text(encoding="utf-8")),
                module=module,
                fallback_output=output_root / _module_slug(module),
            )
            if result.exit_code is None:
                result.exit_code = int(return_code)
            result.checkpoint_path = result_path
            if return_code != 0 and result.status in {"completed", "no_signal"}:
                result.status = "failed"
                result.error = json.dumps(
                    {"type": "child_exit", "exit_code": int(return_code)},
                    sort_keys=True,
                )
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return StrategyResult(
                module=module,
                status="failed",
                duration=time.monotonic() - started,
                output_dir=output_root / _module_slug(module),
                error=f"invalid child result: {exc}",
                exit_code=int(return_code),
                checkpoint_path=result_path,
            )
    return StrategyResult(
        module=module,
        status="failed",
        duration=time.monotonic() - started,
        output_dir=output_root / _module_slug(module),
        error=json.dumps(
            {"type": "child_no_result", "exit_code": int(return_code)},
            sort_keys=True,
        ),
        exit_code=int(return_code),
        checkpoint_path=result_path,
    )


def _run_strategy_targets(
    targets: list[str],
    *,
    output_root: Path,
    keep_artifacts: bool,
    artifact_policy: str,
    timeout_sec: float,
    result_dir: Path,
    summary_writer: SummaryCsvWriter,
    checkpoint_path: Path,
) -> list[StrategyResult]:
    """Run sorted targets one at a time and persist each result immediately."""
    results: list[StrategyResult] = []
    with tqdm(targets, desc="Strategies", unit="strategy", dynamic_ncols=True) as progress:
        for module in progress:
            try:
                result = _run_strategy_subprocess(
                    module,
                    output_root=output_root,
                    keep_artifacts=keep_artifacts,
                    artifact_policy=artifact_policy,
                    timeout_sec=timeout_sec,
                    result_dir=result_dir,
                )
            except KeyboardInterrupt:
                _terminate_active_processes()
                _write_batch_checkpoint(
                    checkpoint_path,
                    targets=targets,
                    results=results,
                    status="interrupted",
                    timeout_sec=timeout_sec,
                )
                raise
            result.checkpoint_path = checkpoint_path
            results.append(result)
            summary_writer.write(result)
            _write_batch_checkpoint(
                checkpoint_path,
                targets=targets,
                results=results,
                status="running",
                timeout_sec=timeout_sec,
            )
            progress.set_postfix_str(f"{_module_slug(module)}:{result.status}")
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量顺序回测多个策略模块，并把结果汇总到 `summary.csv`。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/multi_strategy_backtest.py\n"
            "  python tools/multi_strategy_backtest.py --dir strategy_library/custom --keep-artifacts\n"
            "  python tools/multi_strategy_backtest.py --output-dir output/tools/multi_strategy/manual\n"
            "  python tools/multi_strategy_backtest.py --timeout-sec 1800"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="可选：指定要回测的模块名或后缀；如果首个参数是现有目录且未传 `--dir`，会把它视作策略目录。",
    )
    parser.add_argument("--dir", "--path", dest="strategy_dir", default="", help="策略目录；默认使用 `BACKTEST_STRATEGY_DIR` 或环境变量 `STRATEGY_DIR`。")
    parser.add_argument("--output-dir", default="", help="汇总输出目录；默认取环境变量 `MULTI_STRATEGY_OUTPUT_DIR` 或 `output/tools/multi_strategy/`。")
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=None,
        help="单策略 wall-clock 超时秒数；0 表示不设超时，默认读取 MULTI_STRATEGY_TIMEOUT_SEC 或 1800。",
    )
    keep_group = parser.add_mutually_exclusive_group()
    keep_group.add_argument("--keep-artifacts", action="store_true", default=False, help="保留每个策略的中间产物目录。")
    keep_group.add_argument("--no-keep-artifacts", action="store_true", default=False, help="强制清理每个策略的中间产物目录。")
    return parser


def _parse_cli_options(argv: List[str]) -> BatchCliOptions:
    ns = build_arg_parser().parse_args(list(argv))
    output_dir = Path(ns.output_dir) if ns.output_dir else None
    env_dir = os.environ.get("MULTI_STRATEGY_OUTPUT_DIR", "").strip()
    if output_dir is None and env_dir:
        output_dir = Path(env_dir)
    if output_dir is None:
        output_dir = DEFAULT_BASE_OUTPUT

    keep_artifacts = _bool_env("MULTI_STRATEGY_KEEP_ARTIFACTS", False)
    if ns.keep_artifacts:
        keep_artifacts = True
    if ns.no_keep_artifacts:
        keep_artifacts = False

    targets = list(ns.targets or [])
    strategy_dir = Path(ns.strategy_dir) if ns.strategy_dir else None
    if strategy_dir is None and targets:
        first = _resolve_cli_path(targets[0])
        if first.exists() and first.is_dir():
            strategy_dir = first
            targets = targets[1:]

    env_strategy_dir = os.environ.get("STRATEGY_DIR", "").strip()
    if strategy_dir is None and env_strategy_dir:
        strategy_dir = Path(env_strategy_dir)

    timeout_raw = ns.timeout_sec
    if timeout_raw is None:
        timeout_raw = float(os.environ.get("MULTI_STRATEGY_TIMEOUT_SEC", "1800") or 1800)
    timeout_sec = float(timeout_raw)
    if not math.isfinite(timeout_sec) or timeout_sec < 0:
        raise ValueError("--timeout-sec must be a finite number >= 0")

    return BatchCliOptions(
        output_dir=output_dir,
        keep_artifacts=keep_artifacts,
        strategy_dir=strategy_dir,
        targets=tuple(targets),
        timeout_sec=timeout_sec,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if "--child-run" in args_in:
        return _run_child_cli(args_in)
    try:
        cli = _parse_cli_options(args_in)
    except (SystemExit, ValueError) as exc:
        if isinstance(exc, ValueError):
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2
        return int(exc.code or 0)
    output_dir = _resolve_cli_path(cli.output_dir)
    keep_artifacts = cli.keep_artifacts
    work_output = _default_work_output(output_dir, keep_artifacts=keep_artifacts)
    strategy_dir = None if cli.strategy_dir is None else _resolve_cli_path(cli.strategy_dir)
    rest_args = list(cli.targets)
    if strategy_dir is not None:
        discovered_files = _discover_strategy_files(strategy_dir)
        targets = _select_targets_in_dir(rest_args, strategy_dir, discovered_files)
    else:
        discovered = discover_builtin_modules()
        targets = _select_targets(rest_args, discovered)
    if not targets:
        print("未找到可回测的策略模块")
        return 1

    try:
        _validate_batch_output_dir(output_dir)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    _cleanup_output_root(output_dir, keep_names=_batch_keep_names(keep_artifacts=keep_artifacts))
    os.environ.pop("LOCAL_DATA_DIR", None)
    os.environ.pop("SAMPLE_DATA_DIR", None)
    base_cfg = get_config(reload=True)
    base_errors, _warnings = validate_config(base_cfg, engine_catalog=ENGINE_REGISTRY)
    if not base_errors:
        try:
            get_execution_policy(base_cfg)
        except Exception as exc:
            base_errors = [str(exc)]
    runtime_options = _build_batch_runtime_options(base_cfg, keep_artifacts=keep_artifacts)
    results: List[StrategyResult] = []
    summary_path = output_dir / "summary.csv"
    checkpoint_path = output_dir / "checkpoint.json"
    child_result_dir = output_dir / "_child_results"
    _write_batch_checkpoint(
        checkpoint_path,
        targets=targets,
        results=results,
        status="running",
        timeout_sec=cli.timeout_sec,
    )
    try:
        with SummaryCsvWriter(output_dir) as summary_writer:
            summary_path = summary_writer.path
            if base_errors:
                results = [
                    StrategyResult(
                        module=module,
                        status="config_failed",
                        duration=0.0,
                        output_dir=work_output / _module_slug(module),
                        engine=str(getattr(base_cfg.portfolio, "engine", "") or ""),
                        transaction_cost_enabled=bool(getattr(base_cfg.transaction_cost, "enable", False)),
                        error="; ".join(base_errors),
                    )
                    for module in targets
                ]
                summary_writer.write_many(results)
                _write_batch_checkpoint(
                    checkpoint_path,
                    targets=targets,
                    results=results,
                    status="config_failed",
                    timeout_sec=cli.timeout_sec,
                )
                print(summary_path)
                return 1

            results = _run_strategy_targets(
                targets,
                output_root=work_output,
                keep_artifacts=keep_artifacts,
                artifact_policy=runtime_options.artifact_policy,
                timeout_sec=cli.timeout_sec,
                result_dir=child_result_dir,
                summary_writer=summary_writer,
                checkpoint_path=checkpoint_path,
            )
        _write_batch_checkpoint(
            checkpoint_path,
            targets=targets,
            results=results,
            status="completed",
            timeout_sec=cli.timeout_sec,
        )
        _cleanup_output_root(output_dir, keep_names=_batch_keep_names(keep_artifacts=keep_artifacts))
        print(summary_path)
        return 0 if all(r.status in {"completed", "no_signal"} for r in results) else 1
    except KeyboardInterrupt:
        _terminate_active_processes()
        _write_batch_checkpoint(
            checkpoint_path,
            targets=targets,
            results=results,
            status="interrupted",
            timeout_sec=cli.timeout_sec,
        )
        return 130
    finally:
        _terminate_active_processes()
        if not keep_artifacts:
            _remove_path(work_output)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version as dist_version
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.artifact_schema import RUN_METADATA_SCHEMA_VERSION
from core.caching.price_l2 import PriceParquetL2Cache, default_price_l2_cache_dir


_SENSITIVE_ENV_KEYS = {
    "MYSQL_PASSWORD",
    "TUSHARE_TOKEN",
    "TS_TOKEN",
}


def create_run_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{ts}_{os.getpid()}_{os.urandom(16).hex()}"


def _safe_env_snapshot(keys: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in keys:
        if key not in os.environ:
            continue
        if key in _SENSITIVE_ENV_KEYS:
            out[key] = "<set>"
        else:
            out[key] = os.environ.get(key, "")
    return out


def _truthy(val: str | None, *, default: bool = True) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() not in {"0", "false", "no", "off", ""}


def _run_git(repo_root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    # Porcelain status uses the first two columns for staged/unstaged state.
    # Preserve leading spaces while removing only Git's line terminator.
    return result.stdout.rstrip("\r\n")


def _git_snapshot(repo_root: Path) -> dict[str, Any] | None:
    if not repo_root or not Path(repo_root).exists():
        return None
    head = _run_git(repo_root, ["rev-parse", "HEAD"])
    if not head:
        return None
    branch = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    describe = _run_git(repo_root, ["describe", "--tags", "--always", "--dirty"])
    status = _run_git(repo_root, ["status", "--porcelain"])
    if status is None:
        return None
    dirty = bool(status)
    staged = 0
    unstaged = 0
    untracked = 0
    if status:
        for line in status.splitlines():
            if line.startswith("??"):
                untracked += 1
            else:
                if len(line) >= 1 and line[0] != " ":
                    staged += 1
                if len(line) >= 2 and line[1] != " ":
                    unstaged += 1
    return {
        "commit": head,
        "branch": branch or None,
        "describe": describe or None,
        "dirty": dirty,
        "status": {
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        },
    }


def _hash_file(path: Path, *, max_bytes: int) -> str | None:
    try:
        size = path.stat().st_size
    except Exception:
        return None
    if size > max_bytes:
        return None
    sha = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except Exception:
        return None


def _count_rows(path: Path, *, max_bytes: int) -> int | None:
    try:
        size = path.stat().st_size
    except Exception:
        return None
    if size > max_bytes:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            # 统计行数，减去表头
            count = sum(1 for _ in f)
        if count <= 0:
            return 0
        return max(count - 1, 0)
    except Exception:
        return None


def _file_stats(path: Path, *, hash_max_bytes: int, rows_max_bytes: int) -> dict[str, Any]:
    stats: dict[str, Any] = {"path": str(path)}
    try:
        st = path.stat()
    except Exception:
        stats["exists"] = False
        return stats
    stats["exists"] = True
    stats["size_bytes"] = st.st_size
    stats["mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    stats["sha256"] = _hash_file(path, max_bytes=hash_max_bytes)
    stats["rows"] = _count_rows(path, max_bytes=rows_max_bytes)
    return stats


def _dir_stats(path: Path, *, max_files: int = 200000) -> dict[str, Any]:
    stats: dict[str, Any] = {"path": str(path)}
    if not path.exists() or not path.is_dir():
        stats["exists"] = False
        return stats
    stats["exists"] = True
    try:
        names = sorted(os.listdir(path), key=str.casefold)
    except Exception as e:
        stats["error"] = str(e)
        return stats
    if len(names) > max_files:
        stats["file_count"] = len(names)
        stats["skipped"] = f"too_many_files>{max_files}"
        return stats
    total_bytes = 0
    latest_mtime = None
    earliest_mtime = None
    file_count = 0
    for name in names:
        p = path / name
        if not p.is_file():
            continue
        file_count += 1
        try:
            st = p.stat()
        except Exception:
            continue
        total_bytes += st.st_size
        mtime = st.st_mtime
        latest_mtime = mtime if latest_mtime is None else max(latest_mtime, mtime)
        earliest_mtime = mtime if earliest_mtime is None else min(earliest_mtime, mtime)
    stats["file_count"] = file_count
    stats["total_bytes"] = total_bytes
    if latest_mtime is not None:
        stats["latest_mtime"] = datetime.fromtimestamp(latest_mtime).isoformat(timespec="seconds")
    if earliest_mtime is not None:
        stats["earliest_mtime"] = datetime.fromtimestamp(earliest_mtime).isoformat(timespec="seconds")
    return stats


def _price_l2_cache_manifest(cfg) -> dict[str, Any] | None:
    include = os.environ.get("RUN_META_PRICE_L2_CACHE")
    if include is None:
        include_enabled = _truthy(os.environ.get("PRICE_L2_CACHE"), default=False)
    else:
        include_enabled = _truthy(include, default=False)
    if not include_enabled:
        return None
    cache = PriceParquetL2Cache(default_price_l2_cache_dir(cfg))
    root = cache.root
    stats = _dir_stats(root)
    stats["enabled"] = True
    if root.exists() and root.is_dir():
        stats["available_codes_count"] = len(cache.available_codes())
    return stats


def _build_data_manifest() -> dict[str, Any] | None:
    local_dir_env = os.environ.get("LOCAL_DATA_DIR") or os.environ.get("SAMPLE_DATA_DIR")
    if not local_dir_env:
        return None
    local_dir = Path(local_dir_env).expanduser().resolve()
    manifest: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "local",
        "local_dir": str(local_dir),
    }
    hash_max_mb = int(os.environ.get("DATA_MANIFEST_HASH_MAX_MB", "50") or "50")
    rows_max_mb = int(os.environ.get("DATA_MANIFEST_ROWS_MAX_MB", "50") or "50")
    hash_max_bytes = max(hash_max_mb, 1) * 1024 * 1024
    rows_max_bytes = max(rows_max_mb, 1) * 1024 * 1024
    max_files = int(os.environ.get("DATA_MANIFEST_MAX_FILES", "200000") or "200000")

    manifest["stock_basic"] = _file_stats(
        local_dir / "stock_basic.csv",
        hash_max_bytes=hash_max_bytes,
        rows_max_bytes=rows_max_bytes,
    )
    manifest["prices_dir"] = _dir_stats(local_dir / "prices", max_files=max_files)

    write_manifest = _truthy(os.environ.get("DATA_MANIFEST_WRITE"), default=False)
    if write_manifest:
        manifest_path = local_dir / "manifest.json"
        try:
            payload = json.dumps(manifest, ensure_ascii=True, indent=2)
            manifest_path.write_text(payload, encoding="utf-8")
            manifest["manifest_path"] = str(manifest_path)
        except Exception as e:
            manifest["manifest_error"] = str(e)
    return manifest


_SENSITIVE_CONFIG_KEYS = {
    "password",
    "password_env",
    "api_key",
    "api_key_env",
    "key_env",
    "key_env_alt",
    "token",
    "token_env",
    "token_env_alt",
}


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, list):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _snapshot_value(raw) for key, raw in value.items()}
    return value


def _mask_sensitive_config_values(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key in _SENSITIVE_CONFIG_KEYS and raw_value:
                out[key] = "<set>"
            else:
                out[key] = _mask_sensitive_config_values(raw_value)
        return out
    if isinstance(value, list):
        return [_mask_sensitive_config_values(item) for item in value]
    if isinstance(value, tuple):
        return [_mask_sensitive_config_values(item) for item in value]
    return value


def _config_snapshot(cfg) -> dict[str, Any]:
    raw = asdict(cfg)
    return _snapshot_value(_mask_sensitive_config_values(raw))


def _distribution_version(dist_name: str) -> str | None:
    name = str(dist_name or "").strip()
    if name == "":
        return None
    try:
        value = dist_version(name)
    except PackageNotFoundError:
        return None
    except Exception:
        return None
    value = str(value).strip()
    return value or None


def _version_snapshot() -> dict[str, Any]:
    versions = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for dist_name in (
        "numpy",
        "pandas",
        "joblib",
        "numba",
        "pyarrow",
        "pydantic",
        "SQLAlchemy",
        "PyMySQL",
        "tushare",
    ):
        resolved = _distribution_version(dist_name)
        if resolved:
            versions[dist_name] = resolved
    return versions


def _run_meta_level() -> str:
    raw = str(os.environ.get("RUN_META_LEVEL") or "").strip().lower()
    if raw in {"min", "minimal", "lite"}:
        return "minimal"
    if raw in {"full", "verbose", "audit"}:
        return "full"
    return "standard"


def _should_collect_data_manifest(*, level: str) -> bool:
    explicit = os.environ.get("RUN_META_DATA_MANIFEST")
    if explicit is not None:
        return _truthy(explicit, default=False)
    return level == "full"


_GIT_SNAPSHOT_UNSET = object()


def build_run_metadata(
    cfg,
    *,
    run_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    strategy_module: str | None = None,
    strategy_meta: Any | None = None,
    execution_policy: Any | None = None,
    risk_cfg: Any | None = None,
    extra: dict[str, Any] | None = None,
    git_snapshot: Any = _GIT_SNAPSHOT_UNSET,
) -> dict[str, Any]:
    meta_level = _run_meta_level()
    env_keys = [
        "STRATEGY_MODULE",
        "CLEAR_OUTPUTS",
        "CLEAR_ALL_TABLES",
        "COMPUTE_METRICS",
        "PROFILE_TIMING",
        "RUN_QUIET",
        "RUN_META_GIT",
        "STATE_MACHINE_JIT",
        "OFFSET_DAYS",
        "LOOKBACK_DAYS",
        "SIGNAL_AVAILABLE_AT",
        "EXEC_BUY_TIMING",
        "EXEC_SELL_TIMING",
        "EXEC_SHIFT_T1",
        "EXEC_ALLOW_INTRADAY",
        "APPLY_TIMING_IN_TRADE_FUNCTION",
        "PRECHECK_TRADABLE_TOPN",
        "BUILD_TOPN_TRADE_SEGMENTS_REPORT",
        "TOPN_CANDIDATE_FACTOR",
        "CHECK_TRADE_CALENDAR",
        "TRADE_CALENDAR_MISSING_WARN_RATIO",
        "PYDANTIC_VALIDATE",
        "PYDANTIC_VALIDATE_SAMPLE",
        "DIAGNOSTICS_ENABLED",
        "DIAGNOSTICS_MULTI_START_ENABLED",
        "DIAGNOSTICS_MULTI_START_STEP_DAYS",
        "DIAGNOSTICS_MULTI_START_WINDOW_DAYS",
        "DIAGNOSTICS_MULTI_START_MAX_STARTS",
        "DIAGNOSTICS_ROBUST_LAMBDA",
        "DIAGNOSTICS_ROBUST_GAMMA",
        "DIAGNOSTICS_MAX_TOP1_PROFIT_SHARE",
        "DIAGNOSTICS_MAX_TRADE_RETURN_HHI",
        "DIAGNOSTICS_MIN_MULTI_START_ROBUST_SCORE",
        "LOCAL_DATA_DIR",
        "SAMPLE_DATA_DIR",
        "DATA_MANIFEST_WRITE",
        "RUN_META_LEVEL",
        "RUN_META_DATA_MANIFEST",
        "DATA_MANIFEST_HASH_MAX_MB",
        "DATA_MANIFEST_ROWS_MAX_MB",
        "DATA_MANIFEST_MAX_FILES",
        "PRICE_L2_CACHE",
        "PRICE_L2_CACHE_WRITE",
        "PRICE_L2_CACHE_DIR",
        "RUN_META_PRICE_L2_CACHE",
        "EXPERIMENT_NAME",
        "EXPERIMENT_TAG",
        "SUBMISSION_SUITE_CASE",
        "TUSHARE_TOKEN",
        "TS_TOKEN",
        "TC_ENABLE",
        "TC_COMMISSION_RATE",
        "TC_MIN_COMMISSION",
        "TC_STAMP_DUTY_RATE",
        "TC_SLIPPAGE_BPS",
        "PORTFOLIO_ENGINE",
        "PORTFOLIO_EMPTY_REENTRY_MODE",
        "PORTFOLIO_SLOT_ASSIGNMENT_MODE",
        "PORTFOLIO_ENABLE_GROUP_DIVERSIFY",
        "PORTFOLIO_DIVERSIFY_GROUP_BY",
        "PORTFOLIO_MAX_PER_GROUP_PER_DAY",
        "PORTFOLIO_GROUP_CAP_FALLBACK",
        "PORTFOLIO_INITIAL_CAPITAL",
        "PORTFOLIO_POSITION_SIZE_METHOD",
        "PORTFOLIO_MAX_POSITIONS",
        "PORTFOLIO_MAX_SINGLE_POSITION_PCT",
        "PORTFOLIO_MAX_GROSS_EXPOSURE",
        "PORTFOLIO_CASH_BUFFER_PCT",
        "PORTFOLIO_ALLOW_PARTIAL_FILL",
        "PORTFOLIO_LOT_SIZE",
        "FIXED_REBALANCE_TARGET_WEIGHTS",
        "FIXED_REBALANCE_SYMBOL_TYPE",
        "FIXED_REBALANCE_FREQUENCY",
        "FIXED_REBALANCE_DATES",
        "FIXED_REBALANCE_BUY_PRICE_COL",
        "FIXED_REBALANCE_SELL_PRICE_COL",
        "FIXED_REBALANCE_MARK_PRICE_COL",
        "FIXED_REBALANCE_NORMALIZE_WEIGHTS",
        "TC_NO_TRADE_DAY",
        "TC_LIMIT_ENABLE",
        "TC_LIMIT_UP_PCT",
        "TC_LIMIT_DOWN_PCT",
        "TC_VOLUME_LIMIT",
        "TC_MAX_PARTICIPATION",
        "TC_REQUIRE_NEXT_BAR",
        "BENCHMARK_CODE",
        "BENCHMARK_NAME",
        "BENCHMARK_SOURCE_CODES",
    ]

    meta = {
        "artifact_type": "run_metadata",
        "schema_version": RUN_METADATA_SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "strategy_module": strategy_module,
        "strategy_meta": asdict(strategy_meta) if is_dataclass(strategy_meta) else strategy_meta,
        "backtest": {
            "start_time": start_time,
            "end_time": end_time,
            "offset_days": cfg.backtest.offset_days,
        },
        "config": _config_snapshot(cfg),
        "env_overrides": _safe_env_snapshot(env_keys),
        "versions": _version_snapshot(),
    }
    if git_snapshot is _GIT_SNAPSHOT_UNSET:
        include_git = _truthy(os.environ.get("RUN_META_GIT"), default=(meta_level != "minimal"))
        meta["git"] = _git_snapshot(Path(cfg.paths.repo_root)) if include_git else None
    else:
        meta["git"] = git_snapshot
    if _should_collect_data_manifest(level=meta_level):
        data_manifest = _build_data_manifest()
        if data_manifest is not None:
            meta["data_manifest"] = data_manifest
    price_l2_manifest = _price_l2_cache_manifest(cfg)
    if price_l2_manifest is not None:
        meta["price_l2_cache"] = price_l2_manifest
    if execution_policy is None:
        meta["execution_policy"] = None
    elif hasattr(execution_policy, "to_dict"):
        meta["execution_policy"] = execution_policy.to_dict()
    elif isinstance(execution_policy, dict):
        meta["execution_policy"] = execution_policy
    else:
        meta["execution_policy"] = None
    if risk_cfg is None:
        meta["risk_control"] = None
    elif hasattr(risk_cfg, "to_dict"):
        meta["risk_control"] = risk_cfg.to_dict()
    elif isinstance(risk_cfg, dict):
        meta["risk_control"] = risk_cfg
    else:
        meta["risk_control"] = None
    if extra:
        meta["extra"] = extra
    return meta


def write_run_metadata(output_dir: Path, metadata: dict[str, Any], *, filename: str = "run_meta.json") -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    payload = json.dumps(metadata, ensure_ascii=True, indent=2)
    path.write_text(payload, encoding="utf-8")
    return path

"""Execution orchestration for Web strategy library backtests."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from config import get_config
from web import library_cache_publication, library_evaluation_identity
from web.batch_stock_basic import batch_stock_basic_scope
from web.cache_generation import clear_web_cache_generations
from web.execution import execute_web_backtest
from web.library_cache_identity import _build_cache_config_signature
from web.library_catalog import invalidate_library_catalog_cache, list_library
from web.library_metadata import (
    LIBRARY_ROOT,
    _is_local_strategy_module,
    _parse_strategy_config,
    _resolve_effective_backtest_window,
    _resolve_library_strategy_file,
)
from web.log_service import log_event
from web.run_config import build_library_run_overrides
from web.run_result_payloads import apply_duration, build_library_run_result
from web.task_coordinator import TaskOwnershipError
from web.task_states import BACKTEST_PROGRESS


_progress_state = BACKTEST_PROGRESS


def _start_progress_task(*, total: int):
    """Return a task-bound progress handle, retaining local-test compatibility."""
    start_task = getattr(_progress_state, "start_task", None)
    if callable(start_task):
        return start_task(total=total)
    if _progress_state.start(total=total):
        return _progress_state
    return None


def _checkpoint_progress_task(progress_task: Any) -> None:
    checkpoint = getattr(progress_task, "checkpoint", None)
    if callable(checkpoint):
        checkpoint()


def _run_single(
    name: str,
    date_overrides: dict[str, Any] | None = None,
    *,
    clear_outputs: bool = False,
) -> dict[str, Any]:
    """Run and publish one strategy from the Web strategy library."""
    started_at = time.time()
    strategy_dir = LIBRARY_ROOT / name
    cfg_path = strategy_dir / "config.ini"
    strat_cfg = _parse_strategy_config(cfg_path) if cfg_path.is_file() else {}
    module = strat_cfg.get("strategy.module", "")
    source_path: Path | None = None
    effective_window: dict[str, Any] = {
        "start_time": strat_cfg.get("backtest.start_time", ""),
        "end_time": strat_cfg.get("backtest.end_time", ""),
    }
    resolved_config: dict[str, Any] = {}

    def _failure_meta_base(initial: dict[str, Any] | None = None) -> dict[str, Any]:
        failure_meta = library_cache_publication._normalize_library_cache_meta(
            dict(initial or {}),
            name=name,
            module=module,
            source_path=source_path,
        )
        cache_config = _build_cache_config_signature(
            strat_cfg,
            effective_window=effective_window,
            date_overrides=date_overrides,
            strategy_dir=strategy_dir,
            source_path=source_path,
        )
        return library_evaluation_identity._attach_evaluation_identity(
            failure_meta,
            name=name,
            module=module,
            cache_config=cache_config,
            resolved_config=resolved_config,
            effective_window=effective_window,
            date_overrides=date_overrides,
        )

    result = build_library_run_result(name, module=module)
    log_event(
        "INFO",
        "backtest_strategy",
        "Strategy backtest started",
        name=name,
        module=module,
        date_overrides=date_overrides or {},
    )
    if not module:
        result["status"] = "failed"
        result["error"] = "config.ini missing strategy.module"
        library_cache_publication._write_failure_cache(
            name,
            module="",
            status="failed",
            error=result["error"],
            meta=_failure_meta_base(),
        )
        apply_duration(result, started_at=started_at)
        log_event(
            "ERROR",
            "backtest_strategy",
            "Strategy backtest failed",
            name=name,
            module=module,
            status=result["status"],
            duration_sec=result.get("duration_sec"),
            error=result["error"],
        )
        return result

    source_path = _resolve_library_strategy_file(strategy_dir, module)
    if source_path is None:
        result["status"] = "config_failed"
        result["error"] = (
            "config.ini strategy.module must point to a local strategy file or "
            "a repository strategy_library.* module"
        )
        library_cache_publication._write_failure_cache(
            name,
            module=module,
            status="config_failed",
            error=result["error"],
            meta=_failure_meta_base(),
        )
        apply_duration(result, started_at=started_at)
        log_event(
            "ERROR",
            "backtest_strategy",
            "Strategy backtest failed",
            name=name,
            module=module,
            status=result["status"],
            duration_sec=result["duration_sec"],
            error=result["error"],
        )
        return result

    strategy_dir.mkdir(parents=True, exist_ok=True)

    try:
        base_cfg = get_config(reload=True)
        effective_window = _resolve_effective_backtest_window(
            strat_cfg,
            date_overrides=date_overrides,
        )
        runtime_cfg = dict(strat_cfg)
        # Uploaded Web-only strategies are loaded by path.  Managed strategies
        # retain their dotted canonical module so execution, cache metadata,
        # and source identity all refer to the same repository owner.
        runtime_cfg["strategy.module"] = (
            str(source_path) if _is_local_strategy_module(module) else module
        )
        if date_overrides is None:
            overrides = build_library_run_overrides(
                runtime_cfg,
                output_root=strategy_dir,
            )
        else:
            overrides = build_library_run_overrides(
                runtime_cfg,
                output_root=strategy_dir,
                date_overrides=effective_window,
            )
        if clear_outputs:
            overrides["run.clear_outputs"] = True
        resolved_config = library_evaluation_identity._resolved_execution_config(
            base_cfg,
            overrides,
        )
        execution = execute_web_backtest(
            base_cfg=base_cfg,
            overrides=overrides,
        )
        if execution.status in {"config_failed", "failed"}:
            result["status"] = execution.status
            result["error"] = execution.error
            failure_meta = _failure_meta_base(dict(execution.meta or {}))
            library_cache_publication._write_failure_cache(
                name,
                module=module,
                status=execution.status,
                error=execution.error,
                meta=failure_meta,
            )
            return result
        if execution.status == "completed":
            result["status"] = "completed"
        else:
            result["status"] = "no_signal"

        if execution.run_result is not None:
            meta = library_cache_publication._enrich_meta_with_run_result(
                dict(execution.meta or {}),
                execution.run_result,
                status=result["status"],
            )
            meta = library_cache_publication._normalize_library_cache_meta(
                meta,
                name=name,
                module=module,
                source_path=source_path,
            )
            cache_config = _build_cache_config_signature(
                strat_cfg,
                effective_window=effective_window,
                date_overrides=date_overrides,
                strategy_dir=strategy_dir,
                source_path=source_path,
            )
            meta = library_evaluation_identity._attach_evaluation_identity(
                meta,
                name=name,
                module=module,
                cache_config=cache_config,
                resolved_config=resolved_config,
                effective_window=effective_window,
                date_overrides=date_overrides,
                run_result=execution.run_result,
            )
            library_cache_publication._publish_cache(
                execution.run_result,
                meta,
                strategy_dir,
            )
            outcome_context = getattr(
                getattr(execution, "outcome", None),
                "ctx",
                None,
            )
            library_cache_publication._persist_library_outputs(
                execution.run_result,
                getattr(outcome_context, "cfg", None),
            )
    except Exception as exc:
        result["error"] = str(exc)
        result["status"] = "failed"
        try:
            library_cache_publication._write_failure_cache(
                name,
                module=module,
                status="failed",
                error=result["error"],
                meta=_failure_meta_base(),
            )
        except Exception:
            pass
    finally:
        apply_duration(result, started_at=started_at)
        status = str(result.get("status") or "")
        log_event(
            "ERROR" if status in {"failed", "config_failed"} else "INFO",
            "backtest_strategy",
            "Strategy backtest finished",
            name=name,
            module=module,
            status=status,
            duration_sec=result.get("duration_sec"),
            error=result.get("error"),
        )

    return result


def start_library_backtest(
    names: list[str] | None = None,
    *,
    date_overrides: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Run selected library strategies sequentially in a background thread."""
    entries = list_library()
    available_names = {str(entry.get("name") or "") for entry in entries}
    if names is None:
        names = [entry["name"] for entry in entries]
    else:
        names = [name for name in names if name in available_names]

    if not names:
        return False

    progress_task = _start_progress_task(total=len(names))
    if progress_task is None:
        return False
    log_event(
        "INFO",
        "backtest_batch",
        "Library backtest batch started",
        strategy_count=len(names),
        names=names,
    )

    def _worker() -> None:
        stock_basic_cache = None
        completed_count = 0
        try:
            with batch_stock_basic_scope() as stock_basic_cache:
                for name in names:
                    _checkpoint_progress_task(progress_task)
                    progress_task.set_current(name)

                    try:
                        per_strategy_dates = None
                        if isinstance(date_overrides, dict):
                            per_strategy_dates = date_overrides.get(name)
                        result = _run_single(
                            name,
                            date_overrides=per_strategy_dates,
                        )
                    except Exception as exc:
                        result = {
                            "name": name,
                            "status": "failed",
                            "error": str(exc),
                            "duration_sec": 0.0,
                        }
                        log_event(
                            "ERROR",
                            "backtest_batch",
                            "Strategy backtest raised in worker",
                            name=name,
                            error=str(exc),
                            exception_type=type(exc).__name__,
                        )

                    _checkpoint_progress_task(progress_task)
                    progress_task.append_result(result, deep=True)
                    completed_count += 1
        except TaskOwnershipError as exc:
            log_event(
                "WARNING",
                "backtest_batch",
                "Library backtest worker stopped after task ownership loss",
                error=str(exc),
            )
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            remaining_names = names[completed_count:]
            log_event(
                "ERROR",
                "backtest_batch",
                "Batch setup failed; marking remaining strategies failed",
                error=error,
                exception_type=type(exc).__name__,
                remaining_count=len(remaining_names),
            )
            for name in remaining_names:
                try:
                    _checkpoint_progress_task(progress_task)
                    progress_task.set_current(name)
                    progress_task.append_result(
                        {
                            "name": name,
                            "status": "failed",
                            "error": error,
                            "duration_sec": 0.0,
                        },
                        deep=True,
                    )
                except TaskOwnershipError as ownership_error:
                    log_event(
                        "WARNING",
                        "backtest_batch",
                        "Library backtest worker stopped while recording setup failure",
                        error=str(ownership_error),
                    )
                    break
        finally:
            if stock_basic_cache is not None:
                stats = stock_basic_cache.stats().to_dict()
                log_event(
                    "WARNING" if stats["error"] else "INFO",
                    "backtest_batch",
                    "Batch stock_basic cache summary",
                    **stats,
                )
            try:
                progress = progress_task.finish(deep=True)
            except TaskOwnershipError as exc:
                log_event(
                    "WARNING",
                    "backtest_batch",
                    "Library backtest worker did not finish after task ownership loss",
                    error=str(exc),
                )
                return
            completed = progress["completed"]
            elapsed_sec = progress["elapsed_sec"]
            results = progress["results"]
            failed_count = sum(
                1
                for item in results
                if str(item.get("status") or "") in {"failed", "config_failed"}
            )
            log_event(
                "ERROR" if failed_count else "INFO",
                "backtest_batch",
                "Library backtest batch finished",
                strategy_count=len(names),
                completed=completed,
                failed=failed_count,
                elapsed_sec=elapsed_sec,
            )

    thread = threading.Thread(target=_worker, daemon=True)
    try:
        thread.start()
    except Exception:
        try:
            progress_task.reset(deep=True)
        except TaskOwnershipError:
            pass
        raise
    return True


def clear_library_cache(name: str) -> bool:
    """Delete all current-generation Web cache artifacts for one strategy."""
    strategy_dir = LIBRARY_ROOT / name
    removed = clear_web_cache_generations(strategy_dir)
    if removed:
        invalidate_library_catalog_cache()
    return removed

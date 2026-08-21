"""Market-data update task construction for the Web maintenance console."""
from __future__ import annotations

import re
import sys
import threading
from datetime import datetime
from typing import Any

from web.log_service import log_event
from web.maintenance_common import (
    PROJECT_ROOT,
    MaintenanceRequestError,
    MaintenanceTask,
    checkpoint_task,
    finish_task,
    run_command,
    set_last_task_kind,
    start_maintenance_task,
)
from web.task_coordinator import TaskOwnershipError


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,24}$")


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise MaintenanceRequestError("日期格式必须为 YYYYMMDD 或 YYYY-MM-DD")


def _parse_codes(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else re.split(r"[\s,;，；]+", str(value or ""))
    codes: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        code = str(raw or "").strip().upper()
        if not code:
            continue
        if not _SAFE_CODE.fullmatch(code):
            raise MaintenanceRequestError(f"标的代码无效：{code}")
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _normalize_symbol_type(value: Any) -> str:
    symbol_type = str(value or "stock").strip().lower()
    if symbol_type not in {"stock", "index", "etf"}:
        raise MaintenanceRequestError("标的类型必须是 stock、index 或 etf")
    return symbol_type


def build_data_update_tasks(payload: dict[str, Any]) -> list[MaintenanceTask]:
    start_date = _normalize_date(payload.get("start_date"))
    end_date = _normalize_date(payload.get("end_date"))
    if start_date > end_date:
        raise MaintenanceRequestError("数据更新起始日期不能晚于截止日期")
    raw_max_workers = payload.get("max_workers")
    try:
        max_workers = int(2 if raw_max_workers in {None, ""} else raw_max_workers)
    except (TypeError, ValueError) as exc:
        raise MaintenanceRequestError("并发数必须是整数") from exc
    if not 1 <= max_workers <= 32:
        raise MaintenanceRequestError("并发数必须在 1 到 32 之间")

    dry_run = _as_bool(payload.get("dry_run"))
    rebuild_panel = _as_bool(payload.get("rebuild_panel"), default=True)
    codes = _parse_codes(payload.get("codes"))
    if codes:
        selected_types = [_normalize_symbol_type(payload.get("symbol_type"))]
    else:
        selected_types = [
            symbol_type
            for symbol_type, key, default in (
                ("stock", "update_stock", True),
                ("index", "update_index", True),
                ("etf", "update_etf", False),
            )
            if _as_bool(payload.get(key), default=default)
        ]
    if not selected_types:
        raise MaintenanceRequestError("至少选择一种数据类型")

    update_script = PROJECT_ROOT / "tools" / "updata_data.py"
    panel_script = PROJECT_ROOT / "tools" / "build_panel_parquet.py"
    if not update_script.is_file():
        raise MaintenanceRequestError("数据更新脚本不存在", status_code=500)
    if rebuild_panel and not dry_run and not panel_script.is_file():
        raise MaintenanceRequestError("Panel 缓存脚本不存在", status_code=500)

    labels = {"stock": "股票日线", "index": "指数日线", "etf": "ETF 日线"}
    tasks: list[MaintenanceTask] = []
    for symbol_type in selected_types:
        command = [
            sys.executable,
            str(update_script),
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--max-workers",
            str(max_workers),
        ]
        if dry_run:
            command.append("--dry-run")
        if codes:
            command.extend(["--symbol-type", symbol_type, "--codes", *codes])
        else:
            command.append(f"--{symbol_type}")
        tasks.append(MaintenanceTask(f"update_{symbol_type}", labels[symbol_type], tuple(command)))
    if rebuild_panel and not dry_run:
        tasks.append(MaintenanceTask(
            "rebuild_panel",
            "重建 Panel 缓存",
            (sys.executable, str(panel_script), "--verify"),
        ))
    return tasks


def start_data_update(payload: dict[str, Any]) -> bool:
    tasks = build_data_update_tasks(payload)
    task_lease = start_maintenance_task(total=len(tasks))
    if task_lease is None:
        return False
    set_last_task_kind("data_update", task_lease=task_lease)
    log_event("INFO", "maintenance", "Data update started", tasks=[task.kind for task in tasks])

    def _worker() -> None:
        has_failure = False
        failed_count = 0
        try:
            for task in tasks:
                checkpoint_task(task_lease)
                task_lease.set_current(task.label)
                if task.kind == "rebuild_panel" and has_failure:
                    result = {
                        "task": task.kind,
                        "label": task.label,
                        "status": "skipped",
                        "return_code": None,
                        "duration_sec": 0.0,
                        "stdout_tail": "",
                        "stderr_tail": "前序数据更新失败，已跳过缓存重建。",
                    }
                else:
                    result = run_command(task)
                if result["status"] == "failed":
                    has_failure = True
                    failed_count += 1
                checkpoint_task(task_lease)
                task_lease.append_result(result, deep=True)
        except TaskOwnershipError as exc:
            log_event(
                "WARNING",
                "maintenance",
                "Data update worker stopped after task ownership loss",
                error=str(exc),
            )
        finally:
            finish_task(
                "data_update",
                failed_count=failed_count,
                task_lease=task_lease,
            )

    try:
        threading.Thread(target=_worker, name="web-data-update", daemon=True).start()
    except Exception:
        try:
            task_lease.reset(deep=True)
        except TaskOwnershipError:
            pass
        raise
    return True


__all__ = ["build_data_update_tasks", "start_data_update"]

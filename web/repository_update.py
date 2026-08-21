"""Fail-closed GitHub fast-forward updates for the Web maintenance console."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from web.log_service import log_event
from web.maintenance_common import (
    PROJECT_ROOT,
    MaintenanceRequestError,
    MaintenanceTask,
    checkpoint_task,
    command_environment,
    finish_task,
    run_command,
    set_last_task_kind,
    start_maintenance_task,
)
from web.task_coordinator import TaskOwnershipError


_REMOTE_NAME = "origin"
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def _git_result(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            env=command_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaintenanceRequestError(f"Git 命令执行失败：{exc}", status_code=500) from exc


def _git_text(*args: str, required: bool = True) -> str:
    completed = _git_result(*args)
    if completed.returncode != 0:
        if not required:
            return ""
        detail = (completed.stderr or completed.stdout or "unknown git error").strip()
        raise MaintenanceRequestError(f"Git 状态读取失败：{detail}", status_code=500)
    return completed.stdout.strip()


def _redact_remote_url(remote_url: str) -> str:
    text = str(remote_url or "").strip()
    if "://" not in text:
        return text
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "<invalid remote URL>"
    if not hostname:
        return text
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, parsed.query, parsed.fragment))


def _is_github_remote(remote_url: str) -> bool:
    text = str(remote_url or "").strip()
    if re.match(r"^[^@\s]+@github\.com:", text, flags=re.IGNORECASE):
        return True
    if "://" not in text:
        return False
    try:
        return (urlsplit(text).hostname or "").casefold() == "github.com"
    except ValueError:
        return False


def _configured_update_branch(current_branch: str) -> str:
    configured = str(os.environ.get("WEB_GITHUB_UPDATE_BRANCH") or "").strip()
    if configured:
        branch = configured
    else:
        remote_head = _git_text(
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{_REMOTE_NAME}/HEAD",
            required=False,
        )
        prefix = f"{_REMOTE_NAME}/"
        branch = remote_head[len(prefix):] if remote_head.startswith(prefix) else current_branch
    if (
        not branch
        or not _SAFE_BRANCH.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or branch.endswith(".lock")
    ):
        raise MaintenanceRequestError("GitHub 更新分支配置无效", status_code=500)
    return branch


def _ahead_behind(target_ref: str) -> tuple[int | None, int | None]:
    counts = _git_text("rev-list", "--left-right", "--count", f"HEAD...{target_ref}", required=False)
    parts = counts.split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def get_repository_status() -> dict[str, Any]:
    inside = _git_text("rev-parse", "--is-inside-work-tree", required=False) == "true"
    if not inside:
        return {"available": False, "error": "当前目录不是 Git 工作区", "remote": _REMOTE_NAME}
    branch = _git_text("symbolic-ref", "--quiet", "--short", "HEAD", required=False)
    revision = _git_text("rev-parse", "HEAD", required=False)
    short_revision = _git_text("rev-parse", "--short=12", "HEAD", required=False)
    subject = _git_text("log", "-1", "--format=%s", required=False)
    remote_url = _git_text("remote", "get-url", _REMOTE_NAME, required=False)
    dirty_lines = [line for line in _git_text("status", "--porcelain=v1", required=False).splitlines() if line]
    update_branch = _configured_update_branch(branch) if branch else ""
    target_ref = f"refs/remotes/{_REMOTE_NAME}/{update_branch}" if update_branch else ""
    target_revision = _git_text("rev-parse", target_ref, required=False) if target_ref else ""
    ahead, behind = _ahead_behind(target_ref) if target_revision else (None, None)
    github_remote = _is_github_remote(remote_url)
    diverged = bool(ahead and behind)
    error = ""
    if not branch:
        error = "当前仓库处于 detached HEAD，不能在线更新"
    elif not remote_url:
        error = "未配置 origin 远端"
    elif not github_remote:
        error = "origin 不是 GitHub 仓库"
    elif dirty_lines:
        error = "工作区有未提交改动，请先提交或暂存后再更新"
    elif diverged:
        error = "本地分支与 GitHub 版本已分叉，不能自动快进"
    return {
        "available": bool(branch and remote_url and github_remote),
        "can_update": not error,
        "error": error,
        "branch": branch,
        "revision": revision,
        "short_revision": short_revision,
        "subject": subject,
        "remote": _REMOTE_NAME,
        "remote_url": _redact_remote_url(remote_url),
        "github_remote": github_remote,
        "update_branch": update_branch,
        "target_ref": f"{_REMOTE_NAME}/{update_branch}" if update_branch else "",
        "target_revision": target_revision,
        "ahead": ahead,
        "behind": behind,
        "update_available": bool(behind and not diverged),
        "diverged": diverged,
        "dirty": bool(dirty_lines),
        "dirty_count": len(dirty_lines),
    }


def _validate_version_preflight() -> dict[str, Any]:
    status = get_repository_status()
    if not status.get("available"):
        raise MaintenanceRequestError(str(status.get("error") or "GitHub 更新不可用"), status_code=409)
    if status.get("dirty") or status.get("diverged"):
        raise MaintenanceRequestError(str(status.get("error")), status_code=409)
    if not status.get("branch") or not status.get("update_branch"):
        raise MaintenanceRequestError(str(status.get("error") or "无法确定更新分支"), status_code=409)
    return status


def _skipped_update_result(message: str) -> dict[str, Any]:
    return {
        "task": "git_fast_forward",
        "label": "应用最新版本",
        "status": "skipped",
        "return_code": None,
        "duration_sec": 0.0,
        "stdout_tail": "",
        "stderr_tail": message,
    }


def start_version_update() -> bool:
    preflight = _validate_version_preflight()
    task_lease = start_maintenance_task(total=2)
    if task_lease is None:
        return False
    set_last_task_kind("version_update", task_lease=task_lease)
    update_branch = str(preflight["update_branch"])
    target_ref = f"refs/remotes/{_REMOTE_NAME}/{update_branch}"
    previous_revision = str(preflight["revision"])
    log_event(
        "WARNING",
        "maintenance",
        "GitHub version update started",
        branch=preflight.get("branch"),
        target=f"{_REMOTE_NAME}/{update_branch}",
        previous_revision=previous_revision,
    )

    def _worker() -> None:
        failed_count = 0
        try:
            fetch_task = MaintenanceTask(
                "git_fetch",
                "获取 GitHub 最新版本",
                ("git", "fetch", "--prune", _REMOTE_NAME, update_branch),
            )
            checkpoint_task(task_lease)
            task_lease.set_current(fetch_task.label)
            fetch_result = run_command(fetch_task, timeout=300.0)
            checkpoint_task(task_lease)
            task_lease.append_result(fetch_result, deep=True)
            if fetch_result["status"] == "failed":
                failed_count += 1
                task_lease.append_result(
                    _skipped_update_result("GitHub 获取失败，未修改本地文件。"),
                    deep=True,
                )
                return

            checkpoint_task(task_lease)
            task_lease.set_current("应用最新版本")
            refreshed = _validate_version_preflight()
            ahead = refreshed.get("ahead")
            behind = refreshed.get("behind")
            if behind in {None, 0}:
                result = {
                    **_skipped_update_result(""),
                    "status": "completed",
                    "return_code": 0,
                    "stdout_tail": "当前已是 GitHub 最新版本。",
                    "previous_revision": previous_revision,
                    "revision": refreshed.get("revision"),
                    "restart_required": False,
                }
            elif ahead:
                result = {**_skipped_update_result("本地分支与 GitHub 版本已分叉，未修改本地文件。"), "status": "failed"}
            else:
                result = run_command(MaintenanceTask(
                    "git_fast_forward",
                    "应用最新版本",
                    ("git", "merge", "--ff-only", target_ref),
                ), timeout=120.0)
                if result["status"] == "completed":
                    new_revision = _git_text("rev-parse", "HEAD", required=False)
                    result.update({
                        "previous_revision": previous_revision,
                        "revision": new_revision,
                        "restart_required": new_revision != previous_revision,
                    })
            if result["status"] == "failed":
                failed_count += 1
            checkpoint_task(task_lease)
            task_lease.append_result(result, deep=True)
        except MaintenanceRequestError as exc:
            failed_count += 1
            result = {**_skipped_update_result(str(exc)), "status": "failed"}
            try:
                checkpoint_task(task_lease)
                task_lease.append_result(result, deep=True)
            except TaskOwnershipError:
                pass
        except TaskOwnershipError as exc:
            log_event(
                "WARNING",
                "maintenance",
                "Version update worker stopped after task ownership loss",
                error=str(exc),
            )
        finally:
            finish_task(
                "version_update",
                failed_count=failed_count,
                task_lease=task_lease,
            )

    try:
        threading.Thread(target=_worker, name="web-version-update", daemon=True).start()
    except Exception:
        try:
            task_lease.reset(deep=True)
        except TaskOwnershipError:
            pass
        raise
    return True


__all__ = ["get_repository_status", "start_version_update"]

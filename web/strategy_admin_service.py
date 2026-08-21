"""Safe strategy installation, rename, and recoverable deletion for the Web library."""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from strategies.installation_validation import (
    StrategyInstallationValidationError,
    validate_strategy_package,
)
from web.library_metadata import (
    LIBRARY_ROOT,
    _is_local_strategy_module,
    _parse_strategy_config,
    _read_strategy_description_manifest,
    _resolve_library_strategy_file,
)
from web.log_service import log_event
from web.maintenance_common import MaintenanceRequestError


_WEB_ROOT = Path(__file__).resolve().parent
_ADMIN_DATA_ROOT = _WEB_ROOT / "data" / "admin"
_DELETED_ROOT = _ADMIN_DATA_ROOT / "deleted_strategies"
_UPLOAD_TEMP_ROOT = _ADMIN_DATA_ROOT / "uploads"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024
_MAX_ZIP_FILES = 400
_MAX_TITLE_LENGTH = 160


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_strategy_name(value: Any, *, fallback: str = "uploaded_strategy") -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text.replace("\\", "_").replace("/", "_"))
    text = text.strip("_-")[:80] or fallback
    if not _SAFE_NAME.fullmatch(text):
        raise MaintenanceRequestError("策略名称只能包含字母、数字、下划线和连字符")
    return text


def _checked_strategy_path(name: str) -> Path:
    if not _SAFE_NAME.fullmatch(name):
        raise MaintenanceRequestError("策略名称无效")
    try:
        base = LIBRARY_ROOT.resolve()
        target = (LIBRARY_ROOT / name).resolve()
        target.relative_to(base)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MaintenanceRequestError("策略目录越界") from exc
    return target


def _require_strategy_name(value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_NAME.fullmatch(text):
        raise MaintenanceRequestError("策略名称只能包含 1-80 位字母、数字、下划线和连字符")
    return text


def _normalize_strategy_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise MaintenanceRequestError("显示标题不能为空")
    if len(text) > _MAX_TITLE_LENGTH:
        raise MaintenanceRequestError(f"显示标题不能超过 {_MAX_TITLE_LENGTH} 个字符")
    return text


def _config_title(config_text: str, fallback: str) -> str:
    lines = config_text.splitlines()
    for label in ("显示标题", "策略"):
        pattern = re.compile(rf"^\s*#\s*{label}\s*[:：]\s*(.*?)\s*$")
        for line in lines:
            match = pattern.match(line)
            if match and match.group(1).strip():
                return match.group(1).strip()
    return fallback


def _current_strategy_display_title(directory: Path, config_text: str) -> str:
    fallback = _config_title(config_text, directory.name)
    try:
        # Import lazily so the maintenance service does not become part of the
        # catalog module's import path.
        from web.library_catalog import _build_library_entry

        title = str(_build_library_entry(directory).get("title") or "").strip()
    except Exception:
        return fallback
    return title or fallback


def _replace_config_title(config_text: str, title: str) -> str:
    lines = config_text.splitlines(keepends=True)
    title_line = re.compile(r"^\s*#\s*策略\s*[:：]")
    display_title_line = re.compile(r"^\s*#\s*显示标题\s*[:：]")
    newline = "\r\n" if "\r\n" in config_text else "\n"
    title_index: int | None = None
    display_title_index: int | None = None
    for index, line in enumerate(lines):
        if title_index is None and title_line.match(line):
            title_index = index
        if display_title_index is None and display_title_line.match(line):
            display_title_index = index

    if title_index is None:
        lines.insert(0, f"# 策略: {title}{newline}")
        title_index = 0
        if display_title_index is not None:
            display_title_index += 1
    else:
        ending = (
            "\r\n" if lines[title_index].endswith("\r\n")
            else "\n" if lines[title_index].endswith("\n")
            else newline
        )
        lines[title_index] = f"# 策略: {title}{ending}"

    if display_title_index is None:
        lines.insert(title_index + 1, f"# 显示标题: {title}{newline}")
    else:
        ending = (
            "\r\n" if lines[display_title_index].endswith("\r\n")
            else "\n" if lines[display_title_index].endswith("\n")
            else newline
        )
        lines[display_title_index] = f"# 显示标题: {title}{ending}"
    return "".join(lines)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _strategy_name_conflict(source: Path, new_name: str) -> bool:
    if not LIBRARY_ROOT.is_dir():
        return False
    source_resolved = source.resolve()
    siblings = sorted(
        (path for path in LIBRARY_ROOT.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    )
    return any(
        sibling.name.casefold() == new_name.casefold()
        and sibling.resolve() != source_resolved
        for sibling in siblings
    )


def _rename_strategy_directory(source: Path, target: Path) -> None:
    if source.name == target.name:
        return
    if _strategy_name_conflict(source, target.name):
        raise MaintenanceRequestError("目标策略名称已存在", status_code=409)

    same_entry = False
    if target.exists():
        try:
            same_entry = source.samefile(target)
        except OSError:
            same_entry = False
        if not same_entry:
            raise MaintenanceRequestError("目标策略名称已存在", status_code=409)

    if not same_entry:
        source.rename(target)
        return

    temporary = source.with_name(f"__rename_{uuid4().hex}")
    source.rename(temporary)
    try:
        temporary.rename(target)
    except Exception:
        if temporary.exists() and not source.exists():
            temporary.rename(source)
        raise


def _validate_strategy_payload(directory: Path) -> tuple[str, Path, dict[str, Any]]:
    cfg_path = directory / "config.ini"
    if not cfg_path.is_file():
        raise MaintenanceRequestError("上传内容缺少 config.ini")
    strat_cfg = _parse_strategy_config(cfg_path)
    module = str(strat_cfg.get("strategy.module") or "").strip()
    if not module:
        raise MaintenanceRequestError("config.ini 缺少 [strategy] module")
    if not _is_local_strategy_module(module):
        raise MaintenanceRequestError("上传策略只能引用包内本地 Python 文件")
    source_path = _resolve_library_strategy_file(directory, module)
    if source_path is None:
        raise MaintenanceRequestError("config.ini 指向的策略源码不存在")
    try:
        report = validate_strategy_package(directory, source_path)
    except StrategyInstallationValidationError as exc:
        raise MaintenanceRequestError(str(exc)) from exc
    description = _read_strategy_description_manifest(directory, source_path)
    if description.get("status") != "verified":
        diagnostics = "；".join(str(item) for item in description.get("diagnostics") or [])
        suffix = f"：{diagnostics}" if diagnostics else ""
        raise MaintenanceRequestError(f"上传内容必须包含源码绑定的 LLM 策略描述{suffix}")
    return module, source_path, report


def _reject_canonical_strategy_mutation(directory: Path) -> None:
    """Keep repository-owned strategies outside the Web upload owner boundary."""
    cfg_path = directory / "config.ini"
    if not cfg_path.is_file():
        return
    cfg = _parse_strategy_config(cfg_path)
    module = str(cfg.get("strategy.module") or "").strip()
    if module and not _is_local_strategy_module(module):
        raise MaintenanceRequestError(
            "根 strategy_library 策略由 canonical 源码管理，维护台只允许操作 Web-only 上传策略",
            status_code=409,
        )


def _file_summary(directory: Path) -> dict[str, int]:
    files = sorted(
        (path for path in directory.rglob("*") if path.is_file()),
        key=lambda path: str(path.relative_to(directory)).casefold(),
    )
    return {
        "file_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
    }


def _archive_strategy(target: Path, *, reason: str) -> tuple[str, Path]:
    _DELETED_ROOT.mkdir(parents=True, exist_ok=True)
    archive_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:10]}_{target.name}"
    archive_path = _DELETED_ROOT / archive_id
    target.rename(archive_path)
    log_event(
        "WARNING",
        "admin_strategy",
        "Strategy archived",
        name=target.name,
        archive_id=archive_id,
        reason=reason,
    )
    return archive_id, archive_path


def _install_materialized_strategy(
    *,
    name: str,
    materialized_dir: Path,
    overwrite: bool,
) -> dict[str, Any]:
    target = _checked_strategy_path(name)
    if target.exists() and not overwrite:
        raise MaintenanceRequestError("策略已存在，启用覆盖后再上传", status_code=409)
    if target.exists() and overwrite:
        _reject_canonical_strategy_mutation(target)
    module, source_path, report = _validate_strategy_payload(materialized_dir)
    source_relative = str(source_path.relative_to(materialized_dir)).replace("\\", "/")
    archive_id = ""
    archive_path: Path | None = None
    if target.exists():
        archive_id, archive_path = _archive_strategy(target, reason="overwrite")
    try:
        materialized_dir.rename(target)
    except Exception:
        if archive_path is not None and archive_path.exists() and not target.exists():
            archive_path.rename(target)
        raise
    summary = _file_summary(target)
    warnings = [
        str(issue.get("message") or issue.get("code") or "")
        for issue in report.get("issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "warning"
    ]
    log_event(
        "INFO",
        "admin_strategy",
        "Strategy installed",
        name=name,
        module=module,
        overwrite=bool(archive_id),
        file_count=summary["file_count"],
        size_bytes=summary["size_bytes"],
        warning_count=len(warnings),
    )
    return {
        "name": name,
        "module": module,
        "source_file": source_relative,
        "file_count": summary["file_count"],
        "size_bytes": summary["size_bytes"],
        "overwritten": bool(archive_id),
        "archive_id": archive_id,
        "warnings": warnings,
    }


def _safe_zip_parts(filename: str) -> tuple[str, ...] | None:
    normalized = str(filename or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise MaintenanceRequestError("zip 包含非法路径")
    parts = tuple(part for part in PurePosixPath(normalized).parts if part not in {"", "."})
    if not parts:
        return None
    if any(part == ".." or ":" in part or part.endswith((" ", ".")) for part in parts):
        raise MaintenanceRequestError("zip 包含非法路径")
    if parts[0] == "__MACOSX":
        return None
    return parts


def _extract_zip_upload(upload: Any, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(upload.stream)
    except zipfile.BadZipFile as exc:
        raise MaintenanceRequestError("zip 包无法读取") from exc
    destinations: set[str] = set()
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > _MAX_ZIP_FILES:
            raise MaintenanceRequestError("zip 文件数量过多")
        if sum(int(info.file_size or 0) for info in infos) > _MAX_ZIP_TOTAL_BYTES:
            raise MaintenanceRequestError("zip 解压内容不能超过 50MB")
        for info in infos:
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise MaintenanceRequestError("zip 不能包含符号链接")
            parts = _safe_zip_parts(info.filename)
            if parts is None:
                continue
            destination = target_dir.joinpath(*parts)
            destination_key = str(destination.relative_to(target_dir)).casefold()
            if destination_key in destinations:
                raise MaintenanceRequestError("zip 包含重复文件路径")
            destinations.add(destination_key)
            try:
                destination.resolve().relative_to(target_dir.resolve())
            except (OSError, RuntimeError, ValueError) as exc:
                raise MaintenanceRequestError("zip 解压路径越界") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _select_zip_payload_root(extract_dir: Path) -> Path:
    children = sorted(extract_dir.iterdir(), key=lambda path: path.name.casefold())
    files = [path for path in children if path.is_file()]
    directories = [path for path in children if path.is_dir()]
    if not (extract_dir / "config.ini").is_file() and len(directories) == 1 and not files:
        return directories[0]
    return extract_dir


def _materialize_zip_payload(source_root: Path, target_dir: Path) -> None:
    shutil.copytree(source_root, target_dir)
    cfg_path = target_dir / "config.ini"
    if not cfg_path.is_file():
        raise MaintenanceRequestError("zip 缺少 config.ini")
    if not (target_dir / "strategy_description.json").is_file():
        raise MaintenanceRequestError("zip 缺少 strategy_description.json")


def install_uploaded_strategy(upload: Any, form: Any) -> dict[str, Any]:
    if upload is None or not getattr(upload, "filename", ""):
        raise MaintenanceRequestError("请选择策略文件")
    filename = Path(str(upload.filename)).name
    suffix = Path(filename).suffix.lower()
    name = _normalize_strategy_name(form.get("strategy_name"), fallback=Path(filename).stem)
    overwrite = _as_bool(form.get("overwrite"))
    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    _UPLOAD_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="strategy-", dir=str(_UPLOAD_TEMP_ROOT)) as tmp_name:
        temp_root = Path(tmp_name)
        materialized = temp_root / "materialized"
        if suffix == ".zip":
            extract_dir = temp_root / "extract"
            _extract_zip_upload(upload, extract_dir)
            _materialize_zip_payload(_select_zip_payload_root(extract_dir), materialized)
        else:
            raise MaintenanceRequestError("仅支持上传包含完整策略包的 .zip")
        return _install_materialized_strategy(
            name=name,
            materialized_dir=materialized,
            overwrite=overwrite,
        )


def rename_strategy(name: str, new_name: Any = None, title: Any = None) -> dict[str, Any]:
    strategy_name = _require_strategy_name(name)
    renamed_name = _require_strategy_name(new_name)
    source = _checked_strategy_path(strategy_name)
    target = _checked_strategy_path(renamed_name)
    if not source.is_dir():
        raise MaintenanceRequestError("策略不存在", status_code=404)
    if _strategy_name_conflict(source, renamed_name):
        raise MaintenanceRequestError("目标策略名称已存在", status_code=409)

    config_path = source / "config.ini"
    if not config_path.is_file():
        raise MaintenanceRequestError("策略缺少 config.ini，无法重命名")
    _reject_canonical_strategy_mutation(source)
    try:
        original_config = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MaintenanceRequestError(f"策略配置读取失败：{exc}") from exc
    current_title = _current_strategy_display_title(source, original_config)
    identifier_changed = strategy_name != renamed_name
    if title is None:
        renamed_title = current_title
        updated_config = (
            _replace_config_title(original_config, renamed_title)
            if identifier_changed
            else original_config
        )
        title_changed = False
    else:
        renamed_title = _normalize_strategy_title(title)
        updated_config = _replace_config_title(original_config, renamed_title)
        title_changed = renamed_title != current_title
    config_changed = updated_config != original_config
    if not identifier_changed and not title_changed:
        raise MaintenanceRequestError("策略名称和显示标题均未变化")

    moved = False
    try:
        if identifier_changed:
            _rename_strategy_directory(source, target)
            moved = True
        destination_config = target / "config.ini" if moved else config_path
        if config_changed:
            _atomic_write_text(destination_config, updated_config)
    except Exception:
        # On case-insensitive filesystems a case-only target also makes the
        # original spelling appear to exist, so target existence is the only
        # reliable rollback guard here.
        if moved and target.exists():
            try:
                _rename_strategy_directory(target, source)
            except Exception as rollback_exc:
                log_event(
                    "ERROR",
                    "admin_strategy",
                    "Strategy rename rollback failed",
                    old_name=strategy_name,
                    new_name=renamed_name,
                    error=str(rollback_exc),
                    exception_type=type(rollback_exc).__name__,
                )
        raise

    log_event(
        "INFO",
        "admin_strategy",
        "Strategy renamed",
        old_name=strategy_name,
        new_name=renamed_name,
        title=renamed_title,
        identifier_changed=identifier_changed,
        title_changed=title_changed,
    )
    return {
        "old_name": strategy_name,
        "name": renamed_name,
        "title": renamed_title,
        "renamed": True,
        "identifier_changed": identifier_changed,
        "title_changed": title_changed,
        "cache_preserved": (target if moved else source).joinpath("web_cache").is_dir(),
    }


def delete_strategy(name: str) -> dict[str, Any]:
    strategy_name = _normalize_strategy_name(name)
    target = _checked_strategy_path(strategy_name)
    if not target.is_dir():
        raise MaintenanceRequestError("策略不存在", status_code=404)
    _reject_canonical_strategy_mutation(target)
    archive_id, _ = _archive_strategy(target, reason="delete")
    return {
        "name": strategy_name,
        "deleted": True,
        "recoverable": True,
        "archive_id": archive_id,
    }


__all__ = ["delete_strategy", "install_uploaded_strategy", "rename_strategy"]

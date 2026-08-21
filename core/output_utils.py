from __future__ import annotations

from pathlib import Path
from typing import Callable

from .output_lock import output_run_lock


def clear_output_files(
    cfg,
    *,
    printer: Callable[[str], None] = print,
) -> None:
    """清理输出文件（限定在 config.paths.* 输出目录）。"""
    output_root = Path(cfg.paths.signal_data).parent
    with output_run_lock(output_root, purpose="clear_outputs"):
        _clear_output_files_unlocked(cfg, printer=printer)


def _clear_output_files_unlocked(
    cfg,
    *,
    printer: Callable[[str], None],
) -> None:
    for p in [
        cfg.paths.signal_data,
        cfg.paths.stock_data,
        cfg.paths.trade_data,
        cfg.paths.return_date,
        cfg.paths.return_date_with_returns,
        cfg.paths.net_value_unique_date,
    ]:
        p = Path(p)
        if not p.is_dir():
            continue
        for child in sorted(p.iterdir(), key=lambda x: str(x.name).casefold()):
            if not child.is_file():
                continue
            if child.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
                continue
            try:
                child.unlink()
            except FileNotFoundError:
                continue
            except PermissionError as e:
                printer(f"[WARN] 无法删除文件（权限不足）: {child} error={e}")
            except Exception as e:
                printer(f"[WARN] 删除文件失败: {child} error={e}")
    printer("输出目录清理完成")

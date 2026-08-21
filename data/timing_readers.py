from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core.constants import COL_TRADE_DATE
from core.exceptions import DataValidationError
from core.market_timing import BoardMemberCache

from .market_data import read_daily_from_mysql, read_thsindex_daily_from_mysql


def read_index_for_timing(
    symbol_code: str,
    df: pd.DataFrame,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    columns: list[str] | None = None,
    cfg,
) -> pd.DataFrame:
    """Read index data for strategy timing builders."""
    if start_date is None or end_date is None:
        if df.empty:
            raise ValueError("df is empty; cannot infer index date range.")
        start_ts = pd.Timestamp(df[COL_TRADE_DATE].iloc[0]).normalize()
        end_ts = pd.Timestamp(df[COL_TRADE_DATE].iloc[-1]).normalize()
        if start_date is None:
            start_date = start_ts.strftime("%Y%m%d")
        if end_date is None:
            end_date = end_ts.strftime("%Y%m%d")

    return read_daily_from_mysql(
        str(symbol_code),
        symbol_type="index",
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        cfg=cfg,
    ).copy()


def read_board_index_for_timing(
    ths_index_code: str,
    _df: pd.DataFrame,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    columns: list[str] | None = None,
    cfg,
) -> pd.DataFrame:
    """Read THS board index data for strategy timing builders."""
    return read_thsindex_daily_from_mysql(
        ths_index_code,
        start_date=start_date,
        end_date=end_date,
        columns=columns,
        cfg=cfg,
    )


def read_board_member_dict(path: str | Path | None = None) -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    json_path = Path(path) if path is not None else repo_root / "data" / "ths_index_member_dict.json"
    if not json_path.exists():
        raise FileNotFoundError(f"未找到板块成分文件: {json_path}")
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"读取板块成分 JSON 失败: {json_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError(f"板块成分 JSON 格式错误（需 dict），实际: {type(data).__name__}")
    return data


def create_board_member_cache(
    *,
    member_dict: dict | None = None,
    member_path: str | Path | None = None,
) -> BoardMemberCache:
    return BoardMemberCache(dict(member_dict) if member_dict is not None else read_board_member_dict(member_path))


def get_board_members(ths_index_code: str, *, cache: BoardMemberCache) -> set[str]:
    return cache.get_members(ths_index_code)


def get_all_board_codes(*, cache: BoardMemberCache) -> tuple[str, ...]:
    return cache.get_all_codes()


def require_bool(value: object, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise DataValidationError(f"{name} 无法解析为 bool: {value!r}")


__all__ = [
    "create_board_member_cache",
    "get_all_board_codes",
    "get_board_members",
    "read_board_index_for_timing",
    "read_board_member_dict",
    "read_index_for_timing",
    "require_bool",
]

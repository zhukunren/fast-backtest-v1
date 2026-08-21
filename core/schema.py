from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from core.constants import COL_TRADE_DATE, COL_STOCK_CODE, SIGNAL_COL_DIRECTION
from core.exceptions import DataValidationError


SIGNAL_NORMALIZED_ATTR = "_normalized_signal_frame"
SIGNAL_SORTED_DATE_CODE_ATTR = "_sorted_date_code"
PRICE_NORMALIZED_ATTR = "_normalized_price_frame"


def _extract_sort_columns(sort_spec: object) -> list[str]:
    if sort_spec is None:
        raise DataValidationError("sort_spec 不能为空（需要至少一个排序字段）")
    if isinstance(sort_spec, str):
        tokens = [x.strip() for x in sort_spec.split(",")]
    elif isinstance(sort_spec, (list, tuple)):
        tokens = list(sort_spec)
    else:
        raise DataValidationError(f"sort_spec type not supported: {type(sort_spec).__name__}")

    cols: list[str] = []
    for tok in tokens:
        if tok is None:
            continue
        if isinstance(tok, (list, tuple)) and len(tok) == 2:
            col = str(tok[0]).strip()
        else:
            s = str(tok).strip()
            if s.startswith("-") or s.startswith("+"):
                s = s[1:].strip()
            col = s
        if not col or col.lower() == COL_TRADE_DATE:
            continue
        cols.append(col)
    if not cols:
        raise DataValidationError("sort_spec 不能为空（需要至少一个排序字段）")
    return cols


@dataclass(frozen=True)
class SignalSchema:
    """
    统一的信号列契约。

    required_cols: 必须存在的列
    sort_spec: [(col, asc)] 形式，不含 trade_date
    """

    required_cols: Tuple[str, ...] = (
        COL_TRADE_DATE,
        COL_STOCK_CODE,
        "open",
        "high",
        "low",
        "close",
        SIGNAL_COL_DIRECTION,
    )
    sort_spec: Tuple[Tuple[str, bool], ...] = ()


def ensure_required_columns(df, schema: SignalSchema) -> None:
    missing = [c for c in schema.required_cols if c not in df.columns]
    if missing:
        raise DataValidationError(f"信号缺少必需列: {missing}")


def normalize_sort_spec(spec: Iterable, allow_empty: bool = False) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    for item in spec:
        if not item:
            continue
        if isinstance(item, (list, tuple)) and len(item) == 2:
            col, asc = item
            col = str(col).strip()
            if col and col.lower() != COL_TRADE_DATE:
                out.append((col, bool(asc)))
        else:
            s = str(item).strip()
            if not s or s.lower() == COL_TRADE_DATE:
                continue
            if s.startswith("-"):
                out.append((s[1:].strip(), False))
            elif s.startswith("+"):
                out.append((s[1:].strip(), True))
            else:
                out.append((s, True))
    if not out and not allow_empty:
        raise DataValidationError("sort_spec 不能为空（需要至少一个排序字段，且不含 trade_date）")
    return out


__all__ = [
    "PRICE_NORMALIZED_ATTR",
    "SIGNAL_NORMALIZED_ATTR",
    "SIGNAL_SORTED_DATE_CODE_ATTR",
    "SignalSchema",
    "ensure_required_columns",
    "normalize_sort_spec",
]

from __future__ import annotations

import math
import re
from typing import Tuple

import numpy as np
import pandas as pd

from core.constants import (
    LIMIT_UP_PCT_CYTECH,
    LIMIT_UP_PCT_BEIJING,
    LIMIT_UP_PCT_ST,
    LIMIT_UP_PERCENT_MAIN,
    LIMIT_UP_PERCENT_CYTECH,
    LIMIT_UP_PERCENT_BEIJING,
    LIMIT_UP_PERCENT_ST,
    PRICE_TICK_A_SHARE,
)
from core.exceptions import ConfigurationError

# ST stock code registry: populated at batch backtest startup from stock_name data.
_st_codes: set[str] = set()
_ST_NAME_RE = re.compile(r"^[*＊]?\s*ST", flags=re.IGNORECASE)


def _tick_decimals(tick: float) -> int:
    text = f"{float(tick):.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def round_price_to_tick(price: float, *, tick: float = PRICE_TICK_A_SHARE, mode: str = "nearest") -> float:
    """Round an A-share price to the exchange tick."""
    try:
        p = float(price)
        t = float(tick)
    except Exception:
        return price
    if not math.isfinite(p) or not math.isfinite(t) or t <= 0:
        return p

    scaled = p / t
    mode_norm = str(mode).strip().lower()
    eps = 1e-12
    if mode_norm == "up":
        rounded_units = math.ceil(scaled - eps)
    elif mode_norm == "down":
        rounded_units = math.floor(scaled + eps)
    elif mode_norm in {"nearest", "round"}:
        rounded_units = math.floor(scaled + 0.5 + eps)
    else:
        raise ValueError(f"unsupported tick rounding mode: {mode!r}")
    return round(float(rounded_units) * t, _tick_decimals(t))


def limit_price(prev_close: float, pct: float, *, side: str, tick: float = PRICE_TICK_A_SHARE) -> float:
    """Return the rounded exchange limit price for a previous close and limit pct."""
    raw = float(prev_close) * (1.0 + float(pct) if str(side).strip().lower() == "up" else 1.0 - float(pct))
    return round_price_to_tick(raw, tick=tick, mode="nearest")


def register_st_codes(codes: set[str]) -> None:
    """Register known ST stock codes (uppercase, e.g. '000725.SZ')."""
    _st_codes.clear()
    _st_codes.update(c.upper().strip() for c in codes if c)


def is_st_stock(stock_code: str) -> bool:
    """Check if a stock is ST using the registry."""
    if not _st_codes:
        return False
    return stock_code.strip().upper() in _st_codes


def is_st_name(stock_name: str | None) -> bool:
    """Check whether stock_name matches ST naming pattern."""
    if stock_name is None:
        return False
    name = str(stock_name).strip()
    if not name:
        return False
    return bool(_ST_NAME_RE.match(name))


def infer_limit_pct(
    stock_code: str | None,
    *,
    base_up_pct: float,
    base_down_pct: float,
    stock_name: str | None = None,
) -> Tuple[float, float]:
    """
    根据股票板块/标记推断涨跌停幅度（小数形式）。
    - 创业/科创：19.8%
    - 北交所：29.8%
    - ST：4.9%（若未同时匹配科创/创业/北交所）
    - 其他：使用传入默认值
    """
    if stock_code is None:
        return base_up_pct, base_down_pct
    code = str(stock_code).strip().upper()
    code_no_suffix = code.split(".")[0]
    is_bj = code.endswith(".BJ") or code_no_suffix.startswith("BJ")
    is_kc = code_no_suffix.startswith("688") or code_no_suffix.startswith("689")
    is_cy = code_no_suffix.startswith("300") or code_no_suffix.startswith("301")
    is_st = is_st_stock(code) or is_st_name(stock_name)

    up = base_up_pct
    down = base_down_pct
    if is_kc or is_cy:
        up = LIMIT_UP_PCT_CYTECH
        down = LIMIT_UP_PCT_CYTECH
    if is_bj:
        up = LIMIT_UP_PCT_BEIJING
        down = LIMIT_UP_PCT_BEIJING
    if is_st and not (is_kc or is_cy or is_bj):
        up = LIMIT_UP_PCT_ST
        down = LIMIT_UP_PCT_ST
    return up, down


def resolve_main_limit_percent(
    *,
    limit_up_pct: float | None = None,
    cfg=None,
) -> float:
    """
    读取配置中的主板涨停幅度（百分比）。
    - 优先使用显式传入的 limit_up_pct（小数）
    - 否则读取 cfg.trade_constraints.limit_up_pct
    - 若不可用或非法，回退到默认常量；但 cfg 必须由调用方显式提供。
    """
    if limit_up_pct is not None:
        raw = float(limit_up_pct) * 100.0
        if math.isfinite(raw) and raw > 0:
            return raw
    try:
        if cfg is not None:
            cfg_obj = cfg
        else:
            raise ConfigurationError("resolve_main_limit_percent 需要显式传入 cfg 或 limit_up_pct。")
        raw = float(getattr(cfg_obj.trade_constraints, "limit_up_pct", 0.0)) * 100.0
        if math.isfinite(raw) and raw > 0:
            return raw
    except ConfigurationError:
        raise
    except Exception:
        pass
    return LIMIT_UP_PERCENT_MAIN


def infer_limit_percent_series(
    code_series: pd.Series,
    *,
    base_main_pct: float | None = None,
    name_series: pd.Series | None = None,
) -> pd.Series:
    """
    根据股票代码序列推断涨停幅度（百分比）。
    - 默认主板使用 base_main_pct（未传则使用 LIMIT_UP_PERCENT_MAIN）
    - 创业/科创：19.8%
    - 北交所：29.8%
    - ST：4.9%（若未同时匹配科创/创业/北交所）
    """
    if base_main_pct is None:
        base_main_pct = LIMIT_UP_PERCENT_MAIN

    code = code_series.astype(str).str.upper().str.strip()
    is_bj = code.str.endswith(".BJ") | code.str.startswith("BJ")
    is_kc = code.str.startswith(("688", "689"))
    is_cy = code.str.startswith(("300", "301"))
    kc_cy_mask = (is_kc | is_cy).to_numpy(dtype=bool, copy=False)
    bj_mask = is_bj.to_numpy(dtype=bool, copy=False)
    st_mask = np.zeros(len(code_series), dtype=bool)
    if _st_codes:
        st_mask |= code.isin(_st_codes).to_numpy(dtype=bool, copy=False)
    if name_series is not None:
        st_mask |= name_series.astype(str).str.strip().str.match(_ST_NAME_RE, na=False).to_numpy(
            dtype=bool,
            copy=False,
        )

    limit_pct = np.full(len(code_series), float(base_main_pct), dtype="float64")
    limit_pct[kc_cy_mask] = LIMIT_UP_PERCENT_CYTECH
    limit_pct[bj_mask] = LIMIT_UP_PERCENT_BEIJING
    limit_pct[st_mask & ~(kc_cy_mask | bj_mask)] = LIMIT_UP_PERCENT_ST
    return pd.Series(limit_pct, index=code_series.index)

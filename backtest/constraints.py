from __future__ import annotations

from collections.abc import Mapping
import math
from core.constants import FLOAT_TOLERANCE
from trading.lot_rules import normalize_buy_qty, normalize_sell_qty
from trading.market_rules import infer_limit_pct, limit_price


class BaseTradeConstraint:
    """交易约束基类"""

    def check(
        self,
        *,
        stock_code: str,
        bar_today: dict | None,
        bar_next: dict | None,
        side: str,
        cons_cfg: object,
        current_state: dict | None = None,
    ) -> tuple[bool, str]:
        """
        检查约束是否满足
        返回: (是否通过, 原因)
        """
        raise NotImplementedError


def is_no_trade_day(
    *,
    open_price: float | None,
    high_price: float | None,
    low_price: float | None,
    close_price: float | None,
    volume: float | None = None,
) -> bool:
    """
    更保守的停牌/无量判断：
    - 任一价格缺失/不可解析 → 视为不可成交
    - 成交量缺失/非正数 → 视为不可成交
    """
    prices = [open_price, high_price, low_price, close_price]
    try:
        prices_f = [float(p) for p in prices]
    except Exception:
        return True
    if any((not math.isfinite(p)) for p in prices_f):
        return True
    if any(abs(p) <= FLOAT_TOLERANCE for p in prices_f):
        return True

    try:
        if volume is None:
            return True
        vol_f = float(volume)
    except Exception:
        return True
    if (not math.isfinite(vol_f)) or vol_f <= FLOAT_TOLERANCE:
        return True
    return False


def _limit_price(prev_close: float, pct: float, *, side: str) -> float:
    return limit_price(prev_close, pct, side=side)


def _finite_positive_at(values: object, idx: int) -> float:
    if values is None or idx < 0:
        return math.nan
    try:
        value = float(values[idx])
    except (IndexError, KeyError, TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) and value > FLOAT_TOLERANCE else math.nan


def _execution_previous_close(
    meta: dict,
    *,
    execution_idx: int,
    fallback_idx: int,
) -> float:
    supplied = _finite_positive_at(meta.get("pre_close"), execution_idx)
    if math.isfinite(supplied):
        return supplied
    return _finite_positive_at(meta.get("close"), fallback_idx)



def is_limit_locked(
    *,
    open_price: float | None,
    high_price: float | None,
    low_price: float | None,
    close_price: float | None,
    prev_close: float | None,
    limit_up_pct: float,
    limit_down_pct: float,
    side: str,
    stock_code: str | None = None,
    stock_name: str | None = None,
) -> bool:
    try:
        pc = float(prev_close)
    except Exception:
        return False
    if not math.isfinite(pc) or pc <= 0:
        return False
    try:
        o = float(open_price)
        h = float(high_price)
        low = float(low_price)
        c = float(close_price)
    except Exception:
        return False
    if not all(math.isfinite(x) for x in (o, h, low, c)):
        return False

    limit_up_pct, limit_down_pct = infer_limit_pct(
        stock_code,
        base_up_pct=limit_up_pct,
        base_down_pct=limit_down_pct,
        stock_name=stock_name,
    )

    tol = FLOAT_TOLERANCE
    if side == "buy":
        limit_up = _limit_price(pc, limit_up_pct, side="up")
        return low >= limit_up - tol and c >= limit_up - tol
    if side == "sell":
        limit_down = _limit_price(pc, limit_down_pct, side="down")
        return h <= limit_down + tol and c <= limit_down + tol
    return False


class DiversifyConstraint(BaseTradeConstraint):
    """分散度约束：行业/板块限额"""

    __slots__ = ("max_per_group",)

    def __init__(self, max_per_group: int):
        self.max_per_group = max_per_group

    def check_group(
        self,
        *,
        group_key: str | None,
        group_assign_count: Mapping[str, int],
    ) -> tuple[bool, str]:
        if group_key is None:
            return True, "ok"

        count = int(group_assign_count.get(group_key, 0))
        if count >= self.max_per_group:
            return False, "group_cap_exceeded"

        return True, "ok"

    def check(
        self,
        *,
        stock_code: str,
        bar_today: dict | None,
        bar_next: dict | None,
        side: str,
        cons_cfg: object,
        current_state: dict | None = None,
    ) -> tuple[bool, str]:
        if current_state is None:
            return True, "ok"
        return self.check_group(
            group_key=current_state.get("group_key"),
            group_assign_count=current_state.get("group_assign_count", {}),
        )


class HardRuleConstraint(BaseTradeConstraint):
    """高性能核心约束：涨跌停、停牌、无量、行情缺失 (直接在 meta 上操作以保证性能)"""

    __slots__ = ("buy_is_signal_close",)

    def __init__(self, buy_timing: str):
        self.buy_is_signal_close = (buy_timing == "signal_close")

    def check_meta(
        self,
        *,
        meta: dict,
        idx: int,
        cons_cfg: object,
    ) -> tuple[bool, str]:
        """针对 allocator.py 的优化检查方法，避免频繁构造 dict"""
        exec_idx = idx if self.buy_is_signal_close else idx + 1
        prev_idx = idx - 1 if self.buy_is_signal_close else idx

        if exec_idx > meta["last_idx"]:
            return False, "missing_price"

        # 1. 行情缺失检查
        open_price = meta["open"][exec_idx]
        high_price = meta["high"][exec_idx]
        low_price = meta["low"][exec_idx]
        close_price = meta["close"][exec_idx]
        prev_close = _execution_previous_close(
            meta,
            execution_idx=exec_idx,
            fallback_idx=prev_idx,
        )

        if not (
            math.isfinite(open_price)
            and math.isfinite(high_price)
            and math.isfinite(low_price)
            and math.isfinite(close_price)
        ):
            return (True, "missing_price") if not getattr(cons_cfg, "require_next_bar", True) else (False, "missing_price")

        # 2. 停牌/无量检查
        if getattr(cons_cfg, "enable_no_trade_day", True):
            volume_arr = meta.get("volume")
            volume = volume_arr[exec_idx] if volume_arr is not None else None
            # 简化版 is_no_trade_day
            if volume is None or not math.isfinite(volume) or volume <= FLOAT_TOLERANCE:
                return False, "no_trade_day"
            if any(abs(p) <= FLOAT_TOLERANCE for p in (open_price, high_price, low_price, close_price)):
                return False, "no_trade_day"

        # 3. 涨跌停检查
        if getattr(cons_cfg, "enable_limit", True):
            if math.isfinite(prev_close) and prev_close > FLOAT_TOLERANCE:
                limit_up = limit_price(prev_close, meta.get("limit_up_pct", 0.1), side="up")
                if low_price >= limit_up - FLOAT_TOLERANCE and close_price >= limit_up - FLOAT_TOLERANCE:
                    return False, "limit_locked"

        return True, "ok"

    def check(self, **kwargs) -> tuple[bool, str]:
        # 兼容非热路径调用；allocator 主路径仍应直接调用 check_meta。
        cons_cfg = kwargs.get("cons_cfg")
        if cons_cfg is None:
            raise TypeError("HardRuleConstraint.check requires cons_cfg.")

        meta = kwargs.get("meta")
        idx = kwargs.get("idx")
        if (meta is None) != (idx is None):
            raise TypeError("HardRuleConstraint.check requires meta and idx together.")
        if meta is not None and idx is not None:
            return self.check_meta(
                meta=meta,
                idx=int(idx),
                cons_cfg=cons_cfg,
            )

        return is_tradable(
            bar_today=kwargs.get("bar_today"),
            bar_next=kwargs.get("bar_next"),
            cons=cons_cfg,
            side=str(kwargs.get("side") or "buy"),
            stock_code=kwargs.get("stock_code"),
        )


def is_tradable(
    *,
    bar_today,
    bar_next,
    cons,
    side: str,
    stock_code: str | None = None,
) -> tuple[bool, str]:
    """
    统一的可成交性判定，用于 TopN 预检与槽位分配。

    返回 (ok, reason)，reason 取值：
    - "ok"：可成交
    - "missing_price"：缺少今日或次日行情（require_next_bar=0 时仅告警并放行）
    - "no_trade_day"：停牌/无量
    - "limit_locked"：涨跌停封板
    """
    require_next = getattr(cons, "require_next_bar", True)
    if bar_today is None or bar_next is None:
        if not require_next:
            return True, "missing_price"
        return False, "missing_price"

    def _get(obj, key):
        return obj.get(key) if hasattr(obj, "get") else obj[key]

    try:
        prev_close = _get(bar_today, "close")
        try:
            supplied_pre_close = _get(bar_next, "pre_close")
        except Exception:
            supplied_pre_close = None
        try:
            supplied_pre_close = float(supplied_pre_close)
        except (TypeError, ValueError):
            supplied_pre_close = math.nan
        if math.isfinite(supplied_pre_close) and supplied_pre_close > FLOAT_TOLERANCE:
            prev_close = supplied_pre_close
        open_price = _get(bar_next, "open")
        high_price = _get(bar_next, "high")
        low_price = _get(bar_next, "low")
        close_price = _get(bar_next, "close")
        try:
            volume = _get(bar_next, "volume")
        except Exception:
            volume = None
    except Exception:
        if not require_next:
            return True, "missing_price"
        return False, "missing_price"

    prices = [prev_close, open_price, high_price, low_price, close_price]
    try:
        prices_f = [float(p) for p in prices]
    except Exception:
        if not require_next:
            return True, "missing_price"
        return False, "missing_price"
    if any((not math.isfinite(p)) for p in prices_f):
        if not require_next:
            return True, "missing_price"
        return False, "missing_price"

    if cons.enable_no_trade_day and is_no_trade_day(
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
    ):
        return False, "no_trade_day"

    try:
        stock_name = _get(bar_next, "stock_name")
    except Exception:
        stock_name = None

    if cons.enable_limit and is_limit_locked(
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        prev_close=prev_close,
        limit_up_pct=cons.limit_up_pct,
        limit_down_pct=cons.limit_down_pct,
        side=side,
        stock_code=stock_code,
        stock_name=stock_name,
    ):
        return False, "limit_locked"

    return True, "ok"


def max_trade_qty(
    *,
    volume: float | None,
    participation_rate: float,
    lot_size: int,
    stock_code: str | None = None,
    side: str = "buy",
    position_qty: int | None = None,
) -> int:
    if volume is None:
        return 0
    try:
        vol = float(volume)
    except Exception:
        return 0
    if (not math.isfinite(vol)) or vol <= 0 or participation_rate <= 0:
        return 0
    qty = int(math.floor(vol * participation_rate))
    if str(side).strip().lower() == "sell":
        return normalize_sell_qty(
            stock_code,
            qty,
            position_qty=int(position_qty if position_qty is not None else qty),
            lot_size=lot_size,
        )
    return normalize_buy_qty(stock_code, qty, lot_size=lot_size)

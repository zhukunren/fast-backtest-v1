"""Typed runtime state for rolling portfolio allocation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from core.constants import COL_STOCK_CODE
from core.normalizers import is_empty_value


def _build_slot_groups(num_j: int, num_i: int) -> tuple[int, ...]:
    groups: list[int] = []
    for j in range(1, num_j + 1):
        groups.extend([j] * num_i)
    return tuple(groups)


@dataclass
class EmptyReentryController:
    mode: str
    num_j: int
    num_i: int
    _slot_groups: tuple[int, ...] = field(init=False, repr=False)
    _idle_ready: bool = field(default=False, init=False, repr=False)
    _ramp_start_day_idx: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        mode = str(self.mode or "").strip().lower() or "fill_all"
        self.mode = mode if mode in {"fill_all", "staggered"} else "fill_all"
        self._slot_groups = _build_slot_groups(int(self.num_j), int(self.num_i))

    def eligible_open_slots(
        self,
        *,
        day_idx: int,
        signals_present: bool,
        slots_to_process: Sequence[int],
        rows_list: Sequence[list[dict]],
    ) -> set[int]:
        slots = sorted(int(slot_idx) for slot_idx in slots_to_process)
        if not slots:
            return set()
        if self.mode == "fill_all":
            return set(slots)
        all_empty_available = self._all_slots_empty_available(slots, rows_list)
        if self._ramp_start_day_idx is None:
            if all_empty_available:
                self._idle_ready = True
            if self._idle_ready and signals_present:
                self._ramp_start_day_idx = day_idx
                self._idle_ready = False
                return {slot_idx for slot_idx in slots if self._slot_groups[slot_idx] <= 1}
            return set(slots)
        release_level = min(day_idx - self._ramp_start_day_idx + 1, self.num_j)
        eligible = {slot_idx for slot_idx in slots if self._slot_groups[slot_idx] <= release_level}
        if release_level >= self.num_j:
            self._ramp_start_day_idx = None
            self._idle_ready = all_empty_available and not signals_present
        return eligible

    def _all_slots_empty_available(
        self,
        slots: Sequence[int],
        rows_list: Sequence[list[dict]],
    ) -> bool:
        if len(slots) != len(self._slot_groups):
            return False
        return all(is_empty_value(rows_list[slot_idx][-1].get(COL_STOCK_CODE)) for slot_idx in slots)


def _order_slots_for_assignment(
    slots_to_process: list[int],
    *,
    slot_assignment_mode: str,
    round_robin_cursor: int,
) -> list[int]:
    ordered_slots = sorted(slots_to_process)
    if slot_assignment_mode != "round_robin" or len(ordered_slots) <= 1:
        return ordered_slots
    rotate_by = round_robin_cursor % len(ordered_slots)
    if rotate_by == 0:
        return ordered_slots
    return ordered_slots[rotate_by:] + ordered_slots[:rotate_by]


def _schedule_slot_for_revisit(
    *,
    slot_idx: int,
    rows_list: list[list[dict]],
    trade_day_index: dict[np.datetime64, int],
    avail_by_day_idx: list[list[int]],
) -> None:
    revisit_date_np = np.datetime64(pd.Timestamp(rows_list[slot_idx][-1]["trade_date"]), "ns")
    revisit_idx = trade_day_index.get(revisit_date_np)
    if revisit_idx is not None:
        avail_by_day_idx[revisit_idx].append(slot_idx)


def _prime_slot_schedule(
    *,
    rows_list: list[list[dict]],
    trade_day_index: dict[np.datetime64, int],
    avail_by_day_idx: list[list[int]],
) -> None:
    for slot_idx in range(len(rows_list)):
        _schedule_slot_for_revisit(
            slot_idx=slot_idx,
            rows_list=rows_list,
            trade_day_index=trade_day_index,
            avail_by_day_idx=avail_by_day_idx,
        )


def _prepare_day_slots_for_assignment(
    *,
    day_idx: int,
    date: pd.Timestamp,
    avail_by_day_idx: list[list[int]],
    next_date_map: dict[pd.Timestamp, pd.Timestamp],
    buy_timing: str,
    slot_assignment_mode: str,
    round_robin_cursor: int,
    use_fill_all_reentry: bool,
    reentry_controller: EmptyReentryController,
    signals_present: bool,
    rows_list: list[list[dict]],
) -> tuple[list[int], pd.Timestamp | None, set[int] | None]:
    slots_to_process = avail_by_day_idx[day_idx]
    if not slots_to_process:
        return [], None, None
    avail_by_day_idx[day_idx] = []
    next_date = next_date_map.get(date)
    if next_date is None and buy_timing != "signal_close":
        return [], next_date, None
    ordered_slots = _order_slots_for_assignment(
        slots_to_process,
        slot_assignment_mode=slot_assignment_mode,
        round_robin_cursor=round_robin_cursor,
    )
    if use_fill_all_reentry:
        return ordered_slots, next_date, None
    eligible_open_slots = reentry_controller.eligible_open_slots(
        day_idx=day_idx,
        signals_present=signals_present,
        slots_to_process=ordered_slots,
        rows_list=rows_list,
    )
    return ordered_slots, next_date, eligible_open_slots


def _finalize_slot_for_day(
    *,
    assigned: bool,
    next_date: pd.Timestamp | None,
    rows: list[dict],
    slot_idx: int,
    append_empty_slot_row,
    rows_list: list[list[dict]],
    trade_day_index: dict[np.datetime64, int],
    avail_by_day_idx: list[list[int]],
) -> None:
    if not assigned and next_date is not None:
        append_empty_slot_row(rows, next_date=next_date)
    if next_date is not None:
        _schedule_slot_for_revisit(
            slot_idx=slot_idx,
            rows_list=rows_list,
            trade_day_index=trade_day_index,
            avail_by_day_idx=avail_by_day_idx,
        )


@dataclass(frozen=True, slots=True)
class AllocationInputs:
    cfg: Any
    selection: Any
    quote_store: Any
    input_signal_rows: int
    prepared_signal_rows: int
    signals_by_date: dict[pd.Timestamp, tuple[tuple[str, str], ...]]
    trade_days: list[pd.Timestamp]
    next_date_map: dict[pd.Timestamp, pd.Timestamp]
    trade_days_np: np.ndarray
    trade_day_index: dict[np.datetime64, int]
    full_window_end_np: np.datetime64
    buy_timing: str
    sell_timing: str
    shift_t1_sell: bool
    allow_intraday: bool
    allow_missing_stock_data: bool
    empty_reentry_mode: str
    slot_assignment_mode: str
    diversify_group_by: str
    max_per_group_per_day: int
    group_cap_fallback: str
    use_group_cap: bool
    num_j: int
    num_i: int
    cons_cfg: Any
    out_cols: tuple[str, ...]


@dataclass(slots=True)
class AllocationState:
    dfs_rows: dict[str, list[dict]]
    keys: list[str]
    rows_list: list[list[dict]]
    avail_by_day_idx: list[list[int]]
    reentry_controller: EmptyReentryController
    price_meta_cache: dict[str, dict[str, Any]]
    get_price_meta: Callable[..., Any]
    resolve_segment_end: Callable[..., Any]
    check_meta: Callable[..., Any]
    check_group: Callable[..., Any] | None
    round_robin_cursor: int = 0
    sig_idx: int = 0
    deferred_signals: list[tuple[str, str]] = field(default_factory=list)
    deferred_idx: int = 0
    group_assign_count: dict[str, int] = field(default_factory=dict)

    def reset_day_candidates(self) -> None:
        self.sig_idx = 0
        self.deferred_signals.clear()
        self.deferred_idx = 0
        self.group_assign_count.clear()


@dataclass(slots=True)
class AllocationDiagnostics:
    input_signal_rows: int
    prepared_signal_rows: int
    trade_day_count: int
    slot_count: int
    buy_timing: str
    sell_timing: str
    skip_missing_price: int = 0
    skip_no_next_bar: int = 0
    skip_no_trade: int = 0
    skip_limit: int = 0
    warn_missing_price: int = 0
    group_cap_deferred: int = 0
    group_cap_strict_reject: int = 0
    group_cap_relax_assign: int = 0

    def record_skip(self, reason: str) -> None:
        if reason == "no_next_bar":
            self.skip_no_next_bar += 1
        elif reason == "no_trade_day":
            self.skip_no_trade += 1
        elif reason == "limit_locked":
            self.skip_limit += 1
        else:
            self.skip_missing_price += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "input_signal_rows": self.input_signal_rows,
            "prepared_signal_rows": self.prepared_signal_rows,
            "trade_day_count": self.trade_day_count,
            "slot_count": self.slot_count,
            "buy_timing": self.buy_timing,
            "sell_timing": self.sell_timing,
            "skip_missing_price": self.skip_missing_price,
            "skip_no_next_bar": self.skip_no_next_bar,
            "skip_no_trade": self.skip_no_trade,
            "skip_limit": self.skip_limit,
            "warn_missing_price": self.warn_missing_price,
            "group_cap_deferred": self.group_cap_deferred,
            "group_cap_strict_reject": self.group_cap_strict_reject,
            "group_cap_relax_assign": self.group_cap_relax_assign,
        }


__all__ = [
    "AllocationDiagnostics",
    "AllocationInputs",
    "AllocationState",
    "EmptyReentryController",
    "_finalize_slot_for_day",
    "_prepare_day_slots_for_assignment",
    "_prime_slot_schedule",
]

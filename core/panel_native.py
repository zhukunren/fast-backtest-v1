"""Sealed panel storage, positional indexes, and read-only strategy views."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from core.constants import (
    COL_STOCK_CODE,
    COL_TRADE_DATE,
    SIGNAL_COLS_ALL,
)
from core.exceptions import DataValidationError, StrategyError
from core.invariants import PANEL_SORTED_ATTR

from .panel_contracts import FeatureBatch, SignalBatch


_KEY_COLUMNS = frozenset({COL_STOCK_CODE, COL_TRADE_DATE})
_OHLCV_COLUMNS = frozenset({"open", "high", "low", "close", "volume", "vol"})
_FRAMEWORK_COLUMNS = frozenset(SIGNAL_COLS_ALL)


def _readonly(array: np.ndarray) -> np.ndarray:
    out = np.asarray(array)
    if out.ndim != 1:
        raise DataValidationError("panel arrays must be one-dimensional")
    if not out.flags.c_contiguous:
        out = np.ascontiguousarray(out)
    out.setflags(write=False)
    return out


def _readonly_int(values: np.ndarray, *, dtype: np.dtype) -> np.ndarray:
    return _readonly(np.asarray(values, dtype=dtype))


@dataclass(frozen=True, slots=True)
class PanelIndex:
    row_count: int
    stock_codes: tuple[str, ...]
    stock_id_by_row: np.ndarray
    stock_offsets: np.ndarray
    trade_dates: np.ndarray
    date_id_by_row: np.ndarray
    rows_by_date: np.ndarray
    date_offsets: np.ndarray
    next_row_by_row: np.ndarray

    @classmethod
    def build(
        cls,
        stock_code_by_row: np.ndarray,
        trade_dates: np.ndarray,
        *,
        assume_normalized: bool = False,
    ) -> PanelIndex:
        codes = np.asarray(stock_code_by_row)
        dates = np.asarray(trade_dates, dtype="datetime64[ns]")
        if codes.ndim != 1 or dates.ndim != 1 or len(codes) != len(dates):
            raise DataValidationError(
                "panel keys must be one-dimensional arrays with equal length"
            )
        row_count = len(codes)
        if row_count == 0:
            empty_i32 = _readonly_int(np.empty(0), dtype=np.dtype(np.int32))
            empty_i64 = _readonly_int(np.empty(0), dtype=np.dtype(np.int64))
            offsets = _readonly_int(np.array([0]), dtype=np.dtype(np.int64))
            return cls(
                row_count=0,
                stock_codes=(),
                stock_id_by_row=empty_i32,
                stock_offsets=offsets,
                trade_dates=_readonly(dates),
                date_id_by_row=_readonly_int(np.empty(0), dtype=np.dtype(np.int32)),
                rows_by_date=empty_i64,
                date_offsets=_readonly_int(np.array([0]), dtype=np.dtype(np.int64)),
                next_row_by_row=_readonly_int(np.empty(0), dtype=np.dtype(np.int64)),
            )
        if np.isnat(dates).any():
            raise DataValidationError("panel trade_dates contains NaT")

        if assume_normalized and codes.dtype.kind in {"O", "U", "S"}:
            # ``read_price_panel`` seals frames only after its normalization,
            # duplicate-key, and stable-order checks.  Reusing that canonical
            # string array avoids millions of strip/upper calls during sealing.
            code_text = codes
        else:
            code_text = np.asarray([str(value).strip().upper() for value in codes], dtype=object)
        # Avoid materializing a Python list for a whole-market blank check.
        # ``code_text`` is already a one-dimensional NumPy array here.
        if bool(np.any(code_text == "")):
            raise DataValidationError("panel stock_code contains blank values")
        transitions = np.flatnonzero(code_text[1:] != code_text[:-1]) + 1
        stock_offsets = np.concatenate(
            (np.array([0], dtype=np.int64), transitions.astype(np.int64), np.array([row_count], dtype=np.int64))
        )
        stock_codes = tuple(str(code_text[offset]) for offset in stock_offsets[:-1])
        if list(stock_codes) != sorted(stock_codes, key=str.casefold):
            raise DataValidationError(
                "panel rows must use normalized stock-major order"
            )
        # The panel is stock-major, so both ownership and next-row links can be
        # built in one contiguous operation.  The previous per-stock Python loop
        # repeated slice setup and small allocations thousands of times on large
        # panels.
        stock_lengths = np.diff(stock_offsets)
        stock_id_by_row = np.repeat(
            np.arange(len(stock_codes), dtype=np.int32),
            stock_lengths,
        )
        next_row_by_row = np.arange(1, row_count + 1, dtype=np.int64)
        next_row_by_row[-1] = -1
        last_rows = stock_offsets[1:] - 1
        next_row_by_row[last_rows] = -1

        # In stock-major order, a non-increasing date can only be invalid when
        # the adjacent rows belong to the same stock.  This preserves the old
        # duplicate-vs-descending diagnostics without iterating every group.
        if row_count > 1:
            same_stock = code_text[1:] == code_text[:-1]
            invalid_edges = same_stock & (dates[1:] <= dates[:-1])
            if bool(invalid_edges.any()):
                bad_row = int(np.flatnonzero(invalid_edges)[0]) + 1
                stock_id = int(stock_id_by_row[bad_row])
                if dates[bad_row] == dates[bad_row - 1]:
                    raise DataValidationError(
                        "panel has duplicate stock_code+trade_date rows: "
                        f"stock_code={stock_codes[stock_id]}"
                    )
                raise DataValidationError(
                    f"panel dates must be strictly increasing within stock {stock_codes[stock_id]}"
                )

        unique_dates = np.unique(dates)
        date_id_by_row = np.searchsorted(unique_dates, dates).astype(np.int32, copy=False)
        rows_by_date = np.argsort(date_id_by_row, kind="stable").astype(np.int64, copy=False)
        date_counts = np.bincount(date_id_by_row, minlength=len(unique_dates))
        date_offsets = np.concatenate(
            (np.array([0], dtype=np.int64), np.cumsum(date_counts, dtype=np.int64))
        )

        return cls(
            row_count=row_count,
            stock_codes=stock_codes,
            stock_id_by_row=_readonly_int(stock_id_by_row, dtype=np.dtype(np.int32)),
            stock_offsets=_readonly_int(stock_offsets, dtype=np.dtype(np.int64)),
            trade_dates=_readonly(dates),
            date_id_by_row=_readonly_int(date_id_by_row, dtype=np.dtype(np.int32)),
            rows_by_date=_readonly_int(rows_by_date, dtype=np.dtype(np.int64)),
            date_offsets=_readonly_int(date_offsets, dtype=np.dtype(np.int64)),
            next_row_by_row=_readonly_int(next_row_by_row, dtype=np.dtype(np.int64)),
        )

    @property
    def unique_trade_dates(self) -> np.ndarray:
        if self.row_count == 0:
            return _readonly(np.empty(0, dtype="datetime64[ns]"))
        rows = self.rows_by_date[self.date_offsets[:-1]]
        return _readonly(self.trade_dates[rows])

    def stock_slice(self, stock_id: int) -> slice:
        if stock_id < 0 or stock_id >= len(self.stock_codes):
            raise IndexError(f"stock_id out of range: {stock_id}")
        return slice(
            int(self.stock_offsets[stock_id]),
            int(self.stock_offsets[stock_id + 1]),
        )

    def date_rows(self, date_id: int) -> np.ndarray:
        if date_id < 0 or date_id >= len(self.date_offsets) - 1:
            raise IndexError(f"date_id out of range: {date_id}")
        start = int(self.date_offsets[date_id])
        stop = int(self.date_offsets[date_id + 1])
        return self.rows_by_date[start:stop]


@dataclass(frozen=True, slots=True)
class PricePanel:
    index: PanelIndex
    stock_code_by_row: np.ndarray
    columns: Mapping[str, np.ndarray]

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> PricePanel:
        if not isinstance(frame, pd.DataFrame):
            raise DataValidationError("PricePanel requires pandas.DataFrame input")
        missing = sorted(_KEY_COLUMNS - set(frame.columns), key=str.casefold)
        if missing:
            raise DataValidationError(f"PricePanel is missing key columns: {missing}")
        codes = _readonly(frame[COL_STOCK_CODE].to_numpy(copy=False))
        dates = _readonly(
            frame[COL_TRADE_DATE].to_numpy(dtype="datetime64[ns]", copy=False)
        )
        index = PanelIndex.build(
            codes,
            dates,
            assume_normalized=bool(frame.attrs.get(PANEL_SORTED_ATTR, False)),
        )
        columns: dict[str, np.ndarray] = {
            COL_STOCK_CODE: codes,
            COL_TRADE_DATE: index.trade_dates,
        }
        for name in frame.columns:
            column = str(name)
            if column in columns:
                continue
            values = frame[name].to_numpy(copy=False)
            if len(values) != index.row_count:
                raise DataValidationError(
                    f"panel column {column!r} has invalid length: {len(values)}"
                )
            columns[column] = _readonly(values)
        return cls(
            index=index,
            stock_code_by_row=codes,
            columns=MappingProxyType(columns),
        )

    @property
    def row_count(self) -> int:
        return self.index.row_count

    @property
    def protected_columns(self) -> frozenset[str]:
        return frozenset(self.columns) | _KEY_COLUMNS | _OHLCV_COLUMNS | _FRAMEWORK_COLUMNS

    def quote_store(self, columns: Mapping[str, np.ndarray] | None = None) -> QuoteStore:
        merged = dict(self.columns)
        if columns:
            merged.update(columns)
        return QuoteStore(
            index=self.index,
            stock_code_by_row=self.stock_code_by_row,
            columns=MappingProxyType(merged),
        )


class ColumnStore:
    """Owns derived aligned columns without allowing base-column replacement."""

    __slots__ = ("_columns", "_panel")

    def __init__(self, panel: PricePanel) -> None:
        self._panel = panel
        self._columns: dict[str, np.ndarray] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._columns)

    @property
    def columns(self) -> Mapping[str, np.ndarray]:
        return MappingProxyType(self._columns)

    def column(self, name: str) -> np.ndarray:
        if name in self._columns:
            return self._columns[name]
        try:
            return self._panel.columns[name]
        except KeyError:
            raise KeyError(f"unknown panel column: {name}") from None

    def register(self, name: str, values: np.ndarray, *, owner: str) -> np.ndarray:
        column = str(name).strip()
        if not column:
            raise StrategyError(f"{owner} returned a blank column name")
        if column in self._panel.protected_columns or column in self._columns:
            raise StrategyError(f"{owner} cannot overwrite panel column {column!r}")
        array = np.asarray(values)
        if array.ndim != 1 or len(array) != self._panel.row_count:
            raise StrategyError(
                f"{owner} column {column!r} must be one-dimensional with "
                f"length {self._panel.row_count}; got shape={array.shape}"
            )
        for base in self._panel.columns.values():
            if np.shares_memory(array, base):
                raise StrategyError(
                    f"{owner} column {column!r} shares writable storage with a base column"
                )
        for existing_name, existing in self._columns.items():
            if np.shares_memory(array, existing):
                raise StrategyError(
                    f"{owner} column {column!r} shares storage with {existing_name!r}"
                )
        sealed = _readonly(array)
        self._columns[column] = sealed
        return sealed

    def register_features(self, batch: FeatureBatch) -> None:
        if not isinstance(batch, FeatureBatch):
            raise StrategyError("compute_features(panel) must return FeatureBatch")
        for name, values in batch.columns.items():
            self.register(name, values, owner="compute_features")

    def register_signals(self, batch: SignalBatch) -> tuple[np.ndarray, np.ndarray]:
        if not isinstance(batch, SignalBatch):
            raise StrategyError("compute_signals(panel) must return SignalBatch")
        buy = self._validate_signal_mask(batch.buy_raw, name="buy_raw")
        sell = self._validate_signal_mask(batch.sell_raw, name="sell_raw")
        for name, values in batch.columns.items():
            self.register(name, values, owner="compute_signals")
        return buy, sell

    def _validate_signal_mask(self, values: np.ndarray, *, name: str) -> np.ndarray:
        array = np.asarray(values)
        if array.ndim != 1 or len(array) != self._panel.row_count:
            raise StrategyError(
                f"compute_signals {name} must have length {self._panel.row_count}; "
                f"got shape={array.shape}"
            )
        if array.dtype != np.dtype(bool):
            raise StrategyError(f"compute_signals {name} must use bool dtype")
        for base in self._panel.columns.values():
            if np.shares_memory(array, base):
                raise StrategyError(
                    f"compute_signals {name} shares storage with a base column"
                )
        return _readonly(array)


class PanelView:
    """Read-only strategy view over base and derived positional columns."""

    __slots__ = ("_panel", "_store")

    def __init__(self, panel: PricePanel, store: ColumnStore) -> None:
        self._panel = panel
        self._store = store

    @property
    def row_count(self) -> int:
        return self._panel.row_count

    @property
    def stock_id_by_row(self) -> np.ndarray:
        return self._panel.index.stock_id_by_row

    @property
    def date_id_by_row(self) -> np.ndarray:
        return self._panel.index.date_id_by_row

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(self._panel.columns) + self._store.names

    def column(self, name: str) -> np.ndarray:
        return self._store.column(name)

    def series(self, name: str) -> pd.Series:
        return pd.Series(self.column(name), copy=False, name=name)

    def stock_slice(self, stock_id: int) -> slice:
        return self._panel.index.stock_slice(stock_id)

    def date_rows(self, date_id: int) -> np.ndarray:
        return self._panel.index.date_rows(date_id)


@dataclass(frozen=True, slots=True)
class QuoteStore:
    """Read-only positional quote lookup backed by a sealed PricePanel."""

    index: PanelIndex
    stock_code_by_row: np.ndarray
    columns: Mapping[str, np.ndarray]

    def column(self, name: str) -> np.ndarray:
        try:
            return self.columns[name]
        except KeyError:
            raise KeyError(f"unknown quote column: {name}") from None

    @property
    def stock_codes(self) -> tuple[str, ...]:
        return self.index.stock_codes

    @property
    def trade_days(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.index.unique_trade_dates)

    def stock_id(self, stock_code: str) -> int:
        code = str(stock_code).strip().upper()
        position = int(np.searchsorted(np.asarray(self.stock_codes, dtype=str), code))
        if position >= len(self.stock_codes) or self.stock_codes[position] != code:
            raise KeyError(f"unknown stock_code: {stock_code}")
        return position

    def stock_slice(self, stock_code_or_id: str | int) -> slice:
        stock_id = (
            self.stock_id(stock_code_or_id)
            if isinstance(stock_code_or_id, str)
            else int(stock_code_or_id)
        )
        return self.index.stock_slice(stock_id)

    def position(self, stock_code: str, trade_date: object) -> int:
        stock_slice = self.stock_slice(stock_code)
        date = np.datetime64(pd.Timestamp(trade_date).normalize(), "ns")
        dates = self.index.trade_dates[stock_slice]
        local = int(np.searchsorted(dates, date, side="left"))
        if local >= len(dates) or dates[local] != date:
            return -1
        return int(stock_slice.start or 0) + local

    def next_rows(self, rows: Iterable[int]) -> np.ndarray:
        positions = np.asarray(list(rows) if not isinstance(rows, np.ndarray) else rows, dtype=np.int64)
        if positions.ndim != 1:
            raise DataValidationError("quote row positions must be one-dimensional")
        if len(positions) and (positions.min() < 0 or positions.max() >= self.index.row_count):
            raise DataValidationError("quote row position is out of bounds")
        return self.index.next_row_by_row[positions]

    def to_frame(
        self,
        rows: np.ndarray | Iterable[int] | None = None,
        *,
        columns: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        positions = (
            np.arange(self.index.row_count, dtype=np.int64)
            if rows is None
            else np.asarray(list(rows) if not isinstance(rows, np.ndarray) else rows, dtype=np.int64)
        )
        if positions.ndim != 1:
            raise DataValidationError("quote row positions must be one-dimensional")
        if len(positions) and (
            positions.min() < 0 or positions.max() >= self.index.row_count
        ):
            raise DataValidationError("quote row position is out of bounds")
        names = list(columns) if columns is not None else list(self.columns)
        missing = sorted(set(names) - set(self.columns), key=str.casefold)
        if missing:
            raise KeyError(f"unknown quote columns: {missing}")
        return pd.DataFrame({name: self.columns[name][positions] for name in names})


__all__ = ["ColumnStore", "PanelIndex", "PanelView", "PricePanel", "QuoteStore"]

"""Array contracts exchanged by panel-native strategy and selection stages."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np


def _freeze_mapping(values: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
    return MappingProxyType({str(name): value for name, value in values.items()})


def _freeze_array(values: np.ndarray, *, dtype: np.dtype | None = None) -> np.ndarray:
    array = np.asarray(values, dtype=dtype)
    if array.ndim != 1:
        raise ValueError(f"panel-native arrays must be one-dimensional; got {array.shape}")
    if not array.flags.c_contiguous:
        array = np.ascontiguousarray(array)
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class FeatureBatch:
    """Derived feature columns aligned one-for-one with panel rows."""

    columns: Mapping[str, np.ndarray]

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", _freeze_mapping(self.columns))


@dataclass(frozen=True, slots=True)
class SignalBatch:
    """Raw strategy signals plus optional aligned signal-stage columns."""

    buy_raw: np.ndarray
    sell_raw: np.ndarray
    columns: Mapping[str, np.ndarray]

    def __post_init__(self) -> None:
        object.__setattr__(self, "buy_raw", _freeze_array(self.buy_raw))
        object.__setattr__(self, "sell_raw", _freeze_array(self.sell_raw))
        object.__setattr__(self, "columns", _freeze_mapping(self.columns))


@dataclass(frozen=True, slots=True)
class SelectionBatch:
    """Row-position output of signal finalization, precheck, and TopN."""

    buy_rows: np.ndarray
    tradable_rows: np.ndarray
    candidate_rows: np.ndarray
    selected_rows: np.ndarray
    final_buy: np.ndarray
    final_sell: np.ndarray
    diagnostics: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "buy_rows", _freeze_array(self.buy_rows, dtype=np.dtype(np.int64)))
        object.__setattr__(
            self,
            "tradable_rows",
            _freeze_array(self.tradable_rows, dtype=np.dtype(np.int64)),
        )
        object.__setattr__(
            self,
            "candidate_rows",
            _freeze_array(self.candidate_rows, dtype=np.dtype(np.int64)),
        )
        object.__setattr__(
            self,
            "selected_rows",
            _freeze_array(self.selected_rows, dtype=np.dtype(np.int64)),
        )
        object.__setattr__(self, "final_buy", _freeze_array(self.final_buy, dtype=np.dtype(bool)))
        object.__setattr__(self, "final_sell", _freeze_array(self.final_sell, dtype=np.dtype(bool)))
        object.__setattr__(
            self,
            "diagnostics",
            MappingProxyType(dict(self.diagnostics)),
        )


__all__ = ["FeatureBatch", "SelectionBatch", "SignalBatch"]

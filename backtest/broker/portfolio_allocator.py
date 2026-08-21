from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np
import pandas as pd

from core.exceptions import DataValidationError


def _safe_series(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype="float64")
    if arr.size == 0:
        return arr
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr


def allocate_weights(
    df: pd.DataFrame,
    *,
    method: str = "equal",
    score_col: str | None = None,
    max_positions: int = 0,
) -> list[Tuple[str, float]]:
    if df is None or df.empty:
        return []
    temp = df.copy()
    if "stock_code" not in temp.columns:
        raise DataValidationError("signals missing stock_code")

    if max_positions and max_positions > 0:
        temp = temp.head(int(max_positions))

    method = str(method or "equal").strip().lower()
    if method == "score_weighted" and score_col and score_col in temp.columns:
        scores = np.clip(_safe_series(temp[score_col].to_list()), a_min=0.0, a_max=None)
        if scores.sum() <= 0:
            weights = np.ones(len(temp))
        else:
            weights = scores
    else:
        weights = np.ones(len(temp))

    total = float(weights.sum()) if len(weights) > 0 else 0.0
    if total <= 0:
        weights = np.ones(len(temp))
        total = float(weights.sum())

    weights = weights / total
    out = []
    for code, w in zip(temp["stock_code"].astype(str).tolist(), weights.tolist()):
        out.append((code, float(w)))
    return out


def allocate_code_weights(
    codes: Iterable[str],
    *,
    method: str = "equal",
    scores: Iterable[float] | None = None,
    max_positions: int = 0,
) -> list[Tuple[str, float]]:
    code_list = [str(code) for code in codes]
    if max_positions and max_positions > 0:
        code_list = code_list[: int(max_positions)]
    if not code_list:
        return []

    normalized_method = str(method or "equal").strip().lower()
    if normalized_method == "score_weighted" and scores is not None:
        score_values = list(scores)[: len(code_list)]
        if len(score_values) != len(code_list):
            raise DataValidationError("scores length does not match stock codes")
        weights = np.clip(_safe_series(score_values), a_min=0.0, a_max=None)
        if weights.sum() <= 0:
            weights = np.ones(len(code_list))
    else:
        weights = np.ones(len(code_list))

    total = float(weights.sum())
    if total <= 0:
        weights = np.ones(len(code_list))
        total = float(weights.sum())
    return [
        (code, float(weight))
        for code, weight in zip(code_list, (weights / total).tolist())
    ]


__all__ = ["allocate_code_weights", "allocate_weights"]

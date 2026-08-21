from __future__ import annotations

import pandas as pd


_TRUE_TOKENS = {"1", "true", "yes", "y", "on"}
_FALSE_TOKENS = {"", "0", "false", "no", "n", "off", "nan", "none", "null"}
_NUMERIC_FALSE_TRUE_VALUES = [0, 1, 0.0, 1.0, False, True]


def coerce_timing_flag_series(
    series: pd.Series,
    *,
    column_name: str,
    error_cls: type[Exception] = ValueError,
) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)

    if pd.api.types.is_numeric_dtype(series):
        filled = series.fillna(0)
        invalid_mask = ~filled.isin(_NUMERIC_FALSE_TRUE_VALUES)
        if bool(invalid_mask.any()):
            sample = filled.loc[invalid_mask].head(5).tolist()
            raise error_cls(f"{column_name} 仅支持布尔值或 0/1；检测到非法数值示例：{sample}")
        return filled.astype(bool)

    normalized = series.astype(str).str.strip().str.lower()
    invalid_mask = series.notna() & ~normalized.isin(_TRUE_TOKENS | _FALSE_TOKENS)
    if bool(invalid_mask.any()):
        sample = series.loc[invalid_mask].head(5).tolist()
        raise error_cls(
            f"{column_name} 仅支持布尔值或常见真假字符串；检测到非法值示例：{sample}"
        )
    return pd.Series(normalized.isin(_TRUE_TOKENS).to_numpy(dtype=bool), index=series.index)


__all__ = ["coerce_timing_flag_series"]

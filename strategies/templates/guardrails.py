from __future__ import annotations

import pandas as pd

from strategies.signals import coerce_signal_bool_array
from strategies.validation import validate_sort_spec_columns


def apply_guardrails(
    df: pd.DataFrame,
    buy_cond: pd.Series,
    sell_cond: pd.Series,
    sort_spec,
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """
    固定信号保护逻辑（请勿修改）
    - 严格校验并返回标准 RAW 信号掩码
    - 校验 sort_spec 所需列存在，且买入候选值为非布尔的有限实数

    涨跌停、停牌、无量和 next-bar 可成交性由回测引擎统一处理。
    """
    buy_raw = pd.Series(
        coerce_signal_bool_array(
            buy_cond,
            column_name="buy_cond",
            expected_index=df.index,
        ),
        index=df.index,
        dtype=bool,
        copy=False,
    )
    sell_raw = pd.Series(
        coerce_signal_bool_array(
            sell_cond,
            column_name="sell_cond",
            expected_index=df.index,
        ),
        index=df.index,
        dtype=bool,
        copy=False,
    )
    validate_sort_spec_columns(df, sort_spec, candidate_mask=buy_raw)
    return buy_raw, sell_raw, df


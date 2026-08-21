"""Universe and industry helpers for panel batch execution."""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from core.caching.price_frame import normalize_price_cache_code
from core.logging import log_warn
from core.universe import (
    UniverseResolution,
    build_config_universe_spec,
    build_runtime_symbols_universe_spec,
)
from data import get_all_stocks
from trading.market_rules import register_st_codes


def _normalize_stock_code_key(value: object) -> str:
    return normalize_price_cache_code(value)


def _normalized_nonempty_stock_codes(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    for value in values:
        code = _normalize_stock_code_key(value)
        if code:
            out.append(code)
    return out


def _st_stock_mask(stock_name_series: pd.Series) -> pd.Series:
    return (
        stock_name_series.astype(str)
        .str.strip()
        .str.upper()
        .str.match(r"^[*＊]?\s*ST", na=False)
    )


def _should_attach_industry_to_signals(cfg) -> bool:
    portfolio_cfg = getattr(cfg, "portfolio", None)
    if portfolio_cfg is None:
        return False
    if not bool(getattr(portfolio_cfg, "enable_group_diversify", False)):
        return False
    group_by = str(
        getattr(portfolio_cfg, "diversify_group_by", "none") or ""
    ).strip().lower()
    return group_by == "industry"


def _normalize_stock_industry_frame(
    universe_df: pd.DataFrame,
) -> pd.DataFrame | None:
    if universe_df is None or universe_df.empty:
        return None
    if "stock_code" not in universe_df.columns or "industry" not in universe_df.columns:
        return None

    work_df = universe_df.loc[:, ["stock_code", "industry"]].copy()
    work_df["stock_code"] = work_df["stock_code"].map(_normalize_stock_code_key)
    work_df = work_df.loc[work_df["stock_code"].ne("")]
    return None if work_df.empty else work_df


def _build_stock_industry_map_from_df(
    universe_df: pd.DataFrame,
    *,
    stock_code_list: list[str] | None = None,
) -> dict[str, object]:
    work_df = _normalize_stock_industry_frame(universe_df)
    if work_df is None:
        return {}
    if stock_code_list:
        keep_codes = set(_normalized_nonempty_stock_codes(stock_code_list))
        work_df = work_df.loc[work_df["stock_code"].isin(keep_codes)]
    if work_df.empty:
        return {}

    work_df = work_df.drop_duplicates(subset=["stock_code"], keep="first")
    return {
        stock_code: industry
        for stock_code, industry in work_df[["stock_code", "industry"]].itertuples(
            index=False,
            name=None,
        )
    }


def _load_stock_industry_map(
    *,
    cfg,
    stock_code_list: list[str],
    universe_df: pd.DataFrame | None = None,
) -> dict[str, object]:
    if not stock_code_list:
        return {}
    source = get_all_stocks(cfg=cfg) if universe_df is None else universe_df
    return _build_stock_industry_map_from_df(
        source,
        stock_code_list=stock_code_list,
    )


def build_backtest_universe_df(*, cfg) -> pd.DataFrame:
    all_stocks = get_all_stocks(cfg=cfg)
    st_mask: pd.Series | None = None
    stock_code_series = all_stocks["stock_code"].astype(str)

    if "stock_name" in all_stocks.columns:
        st_mask = _st_stock_mask(all_stocks["stock_name"])
        register_st_codes(
            set(_normalized_nonempty_stock_codes(all_stocks.loc[st_mask, "stock_code"]))
        )

    cond = pd.Series(True, index=all_stocks.index)
    if cfg.backtest.exclude_bj:
        cond &= ~stock_code_series.str.contains("BJ", na=False)
    if cfg.backtest.exclude_st:
        if st_mask is None:
            log_warn("exclude_st=1 但股票池缺少 stock_name 列，已跳过名称 ST 过滤。")
        else:
            cond &= ~st_mask
    if cfg.backtest.universe_prefixes:
        cond &= stock_code_series.str.startswith(cfg.backtest.universe_prefixes)
    return all_stocks.loc[cond].reset_index(drop=True)


def _backtest_stock_code_list(universe_df: pd.DataFrame) -> list[str]:
    return universe_df["stock_code"].astype(str).tolist()


def resolve_backtest_stock_codes(*, cfg) -> list[str]:
    return list(resolve_backtest_universe(cfg=cfg).stock_code_list)


def resolve_backtest_universe(
    *,
    cfg,
    stock_code_list: Iterable[object] | None = None,
    strategy_spec: object | None = None,
) -> UniverseResolution:
    if stock_code_list is not None:
        raw_codes = list(stock_code_list)
        normalized_codes = tuple(_normalized_nonempty_stock_codes(raw_codes))
        return UniverseResolution(
            spec=build_runtime_symbols_universe_spec(
                symbols=normalized_codes,
                cfg=cfg,
                strategy_spec=strategy_spec,
            ),
            stock_code_list=normalized_codes,
            source="runtime.stock_code_list",
            metadata={
                "input_count": len(raw_codes),
                "resolved_count": len(normalized_codes),
            },
        )

    universe_df = build_backtest_universe_df(cfg=cfg)
    stock_codes = tuple(_backtest_stock_code_list(universe_df))
    return UniverseResolution(
        spec=build_config_universe_spec(cfg=cfg, strategy_spec=strategy_spec),
        stock_code_list=stock_codes,
        source="config.backtest",
        metadata={
            "stock_basic_rows_after_filters": int(len(universe_df)),
            "resolved_count": len(stock_codes),
        },
    )


__all__ = [
    "_backtest_stock_code_list",
    "_build_stock_industry_map_from_df",
    "_load_stock_industry_map",
    "_normalized_nonempty_stock_codes",
    "_should_attach_industry_to_signals",
    "build_backtest_universe_df",
    "get_all_stocks",
    "resolve_backtest_stock_codes",
    "resolve_backtest_universe",
]

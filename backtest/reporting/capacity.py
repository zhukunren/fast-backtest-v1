"""Portfolio exposure, capacity, and impact-cost diagnostics."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from core.invariants import ensure_trade_date_normalized


DEFAULT_TARGET_PARTICIPATION_RATES = (0.01, 0.02, 0.05)
DEFAULT_IMPACT_K_BPS = 20.0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    return out if math.isfinite(out) else float(default)


def _quantile(values: pd.Series, q: float) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if numeric.empty:
        return 0.0
    return float(numeric.quantile(float(q)))


def _numeric_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _price_volume_lookup(processed_dfs: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
    if not isinstance(processed_dfs, dict) or not processed_dfs:
        return pd.DataFrame(columns=["trade_date", "stock_code", "volume", "close", "amount"])
    frames: list[pd.DataFrame] = []
    for _, df in sorted(processed_dfs.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        if "trade_date" not in df.columns or "stock_code" not in df.columns:
            continue
        cols = [col for col in ("trade_date", "stock_code", "volume", "close", "amount") if col in df.columns]
        if "volume" not in cols and "amount" not in cols:
            continue
        work = ensure_trade_date_normalized(df.loc[:, cols].copy(), col="trade_date")
        work["stock_code"] = work["stock_code"].astype(str).str.strip().str.upper()
        frames.append(work)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "stock_code", "volume", "close", "amount"])
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(["trade_date", "stock_code"], keep="last")
    return out.reset_index(drop=True)


def _normalize_fills(fills_df: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(fills_df, pd.DataFrame) or fills_df.empty:
        return pd.DataFrame()
    required = {"trade_date", "stock_code", "qty", "price"}
    if required - set(fills_df.columns):
        return pd.DataFrame()
    out = ensure_trade_date_normalized(fills_df.copy(), col="trade_date")
    out["stock_code"] = out["stock_code"].astype(str).str.strip().str.upper()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").abs()
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out["notional"] = (out["qty"] * out["price"]).abs()
    if "cash_delta" in out.columns:
        cash_notional = pd.to_numeric(out["cash_delta"], errors="coerce").abs()
        out["notional"] = out["notional"].where(out["notional"].gt(0), cash_notional)
    return out.dropna(subset=["trade_date", "stock_code", "qty", "price"]).reset_index(drop=True)


def _build_liquidity_diagnostics(
    fills_df: pd.DataFrame | None,
    processed_dfs: dict[str, pd.DataFrame] | None,
    *,
    impact_k_bps: float,
) -> dict[str, Any]:
    fills = _normalize_fills(fills_df)
    if fills.empty:
        return {
            "fill_count": 0,
            "known_liquidity_fill_count": 0,
            "unknown_liquidity_fill_count": 0,
            "total_notional": 0.0,
            "turnover_notional": 0.0,
            "median_participation_rate": 0.0,
            "p95_participation_rate": 0.0,
            "max_participation_rate": 0.0,
            "impact_cost_bps_estimate": 0.0,
            "impact_cost_notional_estimate": 0.0,
            "capacity_scale": {},
        }

    lookup = _price_volume_lookup(processed_dfs)
    if not lookup.empty:
        fills = fills.merge(lookup, on=["trade_date", "stock_code"], how="left", suffixes=("", "_market"))
    else:
        fills["volume"] = np.nan
        fills["amount"] = np.nan
        fills["close"] = np.nan

    market_volume = _numeric_column(fills, "volume")
    market_amount = _numeric_column(fills, "amount")
    market_close = _numeric_column(fills, "close")
    market_notional = market_amount.where(market_amount.gt(0), market_volume * market_close)
    participation_qty = fills["qty"] / market_volume
    participation_notional = fills["notional"] / market_notional
    fills["participation_rate"] = participation_qty.where(participation_qty.gt(0), participation_notional)
    fills["participation_rate"] = fills["participation_rate"].replace([np.inf, -np.inf], np.nan)

    known = fills["participation_rate"].notna() & fills["participation_rate"].ge(0)
    p95_participation = _quantile(fills.loc[known, "participation_rate"], 0.95) if bool(known.any()) else 0.0
    impact_bps = float(impact_k_bps) * np.sqrt(fills.loc[known, "participation_rate"].clip(lower=0.0))
    impact_cost = float((fills.loc[known, "notional"] * impact_bps / 10000.0).sum()) if bool(known.any()) else 0.0
    total_notional = float(pd.to_numeric(fills["notional"], errors="coerce").fillna(0.0).sum())
    impact_bps_weighted = impact_cost / total_notional * 10000.0 if total_notional > 0 else 0.0
    capacity_scale = {
        f"scale_to_{int(rate * 10000):04d}bp_adv": (
            float(rate / p95_participation) if p95_participation > 0 else None
        )
        for rate in DEFAULT_TARGET_PARTICIPATION_RATES
    }

    return {
        "fill_count": int(len(fills)),
        "known_liquidity_fill_count": int(known.sum()),
        "unknown_liquidity_fill_count": int((~known).sum()),
        "total_notional": total_notional,
        "turnover_notional": total_notional,
        "median_participation_rate": _quantile(fills.loc[known, "participation_rate"], 0.50) if bool(known.any()) else 0.0,
        "p95_participation_rate": float(p95_participation),
        "max_participation_rate": _quantile(fills.loc[known, "participation_rate"], 1.00) if bool(known.any()) else 0.0,
        "impact_model": "sqrt_participation",
        "impact_k_bps": float(impact_k_bps),
        "impact_cost_bps_estimate": float(impact_bps_weighted),
        "impact_cost_notional_estimate": float(impact_cost),
        "capacity_scale": capacity_scale,
    }


def _build_exposure_frame(processed_dfs: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
    if not isinstance(processed_dfs, dict) or not processed_dfs:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for slot, df in sorted(processed_dfs.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(df, pd.DataFrame) or df.empty or "trade_date" not in df.columns:
            continue
        work = ensure_trade_date_normalized(df.copy(), col="trade_date")
        work["_slot"] = str(slot)
        if "stock_code" not in work.columns:
            work["stock_code"] = ""
        if "position_value_abs" in work.columns:
            value = pd.to_numeric(work["position_value_abs"], errors="coerce").abs()
        elif {"shares", "close"} <= set(work.columns):
            value = pd.to_numeric(work["shares"], errors="coerce").abs() * pd.to_numeric(work["close"], errors="coerce")
        else:
            continue
        if "equity" in work.columns:
            equity = pd.to_numeric(work["equity"], errors="coerce").abs()
        elif "net_value" in work.columns:
            equity = pd.to_numeric(work["net_value"], errors="coerce").abs()
        else:
            equity = pd.Series(np.nan, index=work.index)
        out = pd.DataFrame(
            {
                "trade_date": work["trade_date"],
                "stock_code": work["stock_code"].astype(str).str.strip().str.upper(),
                "position_value": value,
                "equity": equity,
                "slot": str(slot),
            }
        )
        frames.append(out)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _build_exposure_diagnostics(processed_dfs: dict[str, pd.DataFrame] | None) -> dict[str, Any]:
    exposure = _build_exposure_frame(processed_dfs)
    if exposure.empty:
        return {
            "max_single_name_weight": 0.0,
            "max_gross_exposure": 0.0,
            "avg_gross_exposure": 0.0,
            "active_day_count": 0,
        }
    daily_equity = exposure.groupby("trade_date", sort=False)["equity"].sum(min_count=1).replace(0, np.nan)
    by_name = exposure.groupby(["trade_date", "stock_code"], sort=False)["position_value"].sum().reset_index()
    by_name["daily_equity"] = by_name["trade_date"].map(daily_equity)
    by_name["weight"] = by_name["position_value"] / by_name["daily_equity"]
    daily_gross = by_name.groupby("trade_date", sort=False)["weight"].sum(min_count=1).replace([np.inf, -np.inf], np.nan)
    return {
        "max_single_name_weight": _quantile(by_name["weight"], 1.0),
        "max_gross_exposure": _quantile(daily_gross, 1.0),
        "avg_gross_exposure": float(daily_gross.dropna().mean()) if not daily_gross.dropna().empty else 0.0,
        "active_day_count": int(daily_gross.dropna().shape[0]),
    }


def build_capacity_diagnostics(
    *,
    fills_df: pd.DataFrame | None,
    processed_dfs: dict[str, pd.DataFrame] | None,
    impact_k_bps: float = DEFAULT_IMPACT_K_BPS,
) -> dict[str, Any]:
    liquidity = _build_liquidity_diagnostics(
        fills_df,
        processed_dfs,
        impact_k_bps=float(impact_k_bps),
    )
    exposure = _build_exposure_diagnostics(processed_dfs)
    return {
        **liquidity,
        "exposure": exposure,
    }


__all__ = ["DEFAULT_IMPACT_K_BPS", "DEFAULT_TARGET_PARTICIPATION_RATES", "build_capacity_diagnostics"]

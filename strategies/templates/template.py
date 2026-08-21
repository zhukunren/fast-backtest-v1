"""REFERENCE_ONLY: formula-bearing rule strategy and runtime regression fixture.

Runnable rule-first reference strategy for V1 daily TopN backtests.

AUTHORING_STATUS:
- `strategies/templates/iterative_template.py` is the unique default entrypoint.
- This module deliberately contains a complete example for runtime regression.
  Its indicators, hypothesis, dates, and section boundaries are references, not
  defaults or restrictions to copy.
- Capabilities, fields, scaffold, and check are optional discovery/diagnostic
  tools. An ordinary OHLCV strategy does not need to run them before backtesting.

REFERENCE_NAVIGATION: feature_engine, market_feature_sources, entry_exit_rules, priority_score, index_timing, board_timing

SCOPE:
- This template is for V1 daily TopN stock strategies.
- Strategy files generate features, buy candidates, sell intents, and sortable
  priority columns. The engine owns execution, holdings, costs, risk controls,
  TopN allocation, A-share trading rules, and NAV.
- Codex may read the whole repository and all run artifacts. This module is a
  high-signal reference, not a closed interface or required starting point.

CAPABILITY_FREEDOM:
- Every formula and indicator below is replaceable example scaffolding, not a
  required strategy pattern.
- During research, Codex may inspect all repository source, tests, data, and
  artifacts and use any available backtest, validation, diagnostic, profiling,
  or experiment tool. TOOL_MAP is not exhaustive.
- Strategy hooks may define helper functions or classes and use available
  project APIs or installed libraries for any causal and reproducible
  computation, including custom indicators, statistics, models, state or
  sequence rules, and event logic.
- Every curve/assess strategy receives one complete panel. Use grouped
  stock-local operations for time-series formulas and direct cross-section
  operations when whole-market vectorization materially helps.
- Research tools belong in the authoring and analysis workflow.
- Do not launch CLIs or subprocesses or perform external writes from per-symbol
  or panel hot-path hooks.
- Only causality, input invariants, output contracts, and engine ownership of
  execution, holdings, costs, allocation, risk controls, and NAV are fixed.

CAPABILITY_MAP:
- Stock-local time-series rules:
  edit feature_engine() and entry_exit_rules(); sort by stock_code/trade_date and
  use grouped rolling/shift/pct_change so stock boundaries are explicit.
- Cross-sectional ranks:
  set STRATEGY_META["use_market_features"] = True, configure
  market_rank_specs, generate mf_* source columns in market_feature_sources(),
  then use injected rank_col/pct_col/count_col in entry_exit_rules() or
  priority_score().
- Market breadth / daily aggregates:
  set use_market_features=True, configure market_aggregate_specs, generate
  mf_* source columns such as mf_is_up or mf_is_strong, then use injected
  market_* columns in entry_exit_rules() or priority_score().
- Index / board timing:
  set use_index_timing/use_board_timing/require_board_member and edit the
  timing hooks. The framework applies timing filters after signal generation.
- Candidate priority:
  buy_cond=True means "eligible candidate"; score only ranks same-day
  candidates for TopN selection and is not a position weight.
- Full-panel hooks:
  implement `compute_features(PanelView)` plus `compute_signals(PanelView)` and
  use grouped pandas or array-native operations. Return aligned `FeatureBatch`
  and `SignalBatch` arrays; the framework owns final signal filtering and execution.

TOOL_MAP (optional common entry points, not a whitelist or prerequisite):
- codex_strategy.py fields/capabilities/scaffold/check: inspect, generate, and diagnose.
- codex_strategy.py backtest/validate: canonical trusted-local execution entry.
- codex_campaign.py: optional candidate/campaign state and shortlist tooling.
- strategy_static_check.py / strategy_backtest_smoke.py: fast authoring checks.
- benchmark_v1_panel_native.py / inspect_run_artifacts.py: performance and evidence.

FLOW:
- feature_engine -> entry_exit_rules -> priority_score -> raw-mask guardrails -> SignalBatch

HARD_RULES:
- Do not write direction/buy_signal_raw/sell_signal_raw/buy_signal/sell_signal/next_up_limit_ok.
- Return boolean buy_cond/sell_cond Series with exactly the working DataFrame index.
- Panel formulas preserve every input trade_date/stock_code row, its order,
  and original OHLCV. Hooks return only row-aligned FeatureBatch/SignalBatch
  arrays and cannot replace keys, OHLCV, or framework-owned columns.
- Do not implement a portfolio state machine, cash curve, matching engine, or
  fee model inside a strategy file.
- Do not use future data: no shift(-1), negative pct_change, centered rolling,
  future windows, or t+1 prices in t-day signals.
- Do not do cross-sectional rank/aggregate inside a stock-local transform; use
  market_rank_specs and market_aggregate_specs so the framework can build the
  full panel first.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.api import (
    BaseStrategy,
    FeatureBatch,
    PanelView,
    SignalBatch,
    build_board_time_ok as _build_board_time_ok,
    build_index_time_ok,
    compute_feature_batch,
    compute_signal_batch,
)


# SWITCH_GUIDE:
# - use_market_features=False:
#   Use only stock-local time-series features. Do not read injected
#   *_rank/*_pct_rank/*_rank_count or market_* columns.
# - use_market_features=True:
#   Requires at least one market_rank_specs or market_aggregate_specs entry.
#   market_feature_sources() must generate every declared feature_col/source_col.
#   The framework builds the full market panel, then injects rank/pct/count and
#   market_* aggregate columns back into _compute_panel_signals().
# - market_rank_specs:
#   Cross-sectional rank contract. feature_col should normally be mf_* and must
#   not be the final TopN score. Use pct_col in rules when "top x%" matters.
# - market_aggregate_specs:
#   Market breadth contract. source_col is a stock-local mf_* numeric/boolean
#   source; output_col is the per-date market_* aggregate injected later.
# - market_feature_passthrough_cols:
#   Extra mf_* columns copied from market_feature_sources() into _compute_panel_signals()
#   without rank or aggregation.
# - use_index_timing:
#   Enables build_index_timing_signal(index_df). The framework filters new buys
#   after raw signal generation; existing positions remain engine-managed.
# - use_board_timing / require_board_member:
#   board_index_codes selects board indices. require_board_member filters
#   candidates to board members; use_board_timing filters new buys by board date.
# - risk_control:
#   Engine-level new-position scaling. Do not implement cash, holdings, or NAV
#   state inside this strategy template. Query nav_risk/drawdown_risk with
#   `codex_strategy.py capabilities --runtime v1 --id ...` for thresholds and ratios;
#   parsing truth remains in the strategies.api contract implementation.
STRATEGY_META = {
    "strategy_contract_version": "2.0",
    "execution_mode": "panel",
    "name": "template_rule_first_topn",
    "hypothesis": "Rule-based trend, momentum, liquidity, and risk filters can produce auditable TopN candidates.",
    "required_fields": ["trade_date", "stock_code", "open", "high", "low", "close", "volume"],
    "universe_assumption": {
        "source": "runtime",
        "locked": False,
        "notes": "No hard-coded symbols; the runner supplies the actual UniverseSpec.",
    },
    "validation": {
        "train_period": ["2020-01-01", "2023-12-31"],
        "test_period": ["2024-01-01", "2025-12-31"],
    },
    "expected_market_regimes": ["trend", "risk_on"],
    "sort_spec": ["-score"],
    "portfolio_num_j": 5,
    "portfolio_num_i": 2,
    # Enable this only when market_rank_specs or market_aggregate_specs is non-empty.
    "use_market_features": False,
    # Cross-sectional rank example:
    # [{
    #     "feature_col": "mf_rank_score",
    #     "rank_col": "mf_rank",
    #     "pct_col": "mf_pct_rank",
    #     "count_col": "mf_rank_count",
    #     "ascending": False,
    # }]
    "market_rank_specs": [],
    # Market breadth example:
    # [{"source_col": "mf_is_up", "output_col": "market_pos_ratio", "agg": "mean"}]
    "market_aggregate_specs": [],
    # Extra mf_* columns to inject unchanged into _compute_panel_signals.
    "market_feature_passthrough_cols": [],
    "use_index_timing": False,
    "index_code": "399006.SZ",
    "use_board_timing": False,
    "require_board_member": False,
    "board_index_codes": [],
    "risk_control": {
        "enable_nv": False,
        "enable_dd": False,
    },
}


STRATEGY_SEMANTICS = {
    "strategy_type": "rule_based_topn",
    "holding_period": "medium",
    "market": "A_share",
    "timeframe": "daily",
    "primary_signals": ["trend", "momentum", "liquidity", "risk_filter"],
    "risk_profile": "balanced",
}


STRATEGY_DESCRIPTION = {
    "entry": "Rules define buy_cond directly from time-series features, optional market ranks, and optional market breadth.",
    "exit": "Rules define sell_cond directly from trend failure, momentum decay, or risk deterioration.",
    "ranking": "score is candidate priority for same-day TopN selection after buy_cond is true; it is not a position weight.",
    "market_features": "Optional mf_* source columns feed framework-generated cross-sectional rank and market_* breadth columns.",
    "risk": "Execution, T+1, tradability filters, costs, allocation, risk control, and NAV are handled by the engine.",
    "data_assumptions": "Signals are formed after the t-day close and must not use future data.",
}


# Optional advisory research notes, not runtime limits, intentionally empty in the reference.
# Codex strategy production is not constrained by indicator count, code size,
# rolling-call count, condition count, or cyclomatic complexity budgets.
STRATEGY_LIMITS = {}


def _declared_market_feature_columns() -> list[str]:
    """Return market feature columns that _compute_panel_features must expose."""
    rank_specs = STRATEGY_META.get("market_rank_specs", []) or []
    if isinstance(rank_specs, dict):
        rank_specs = [rank_specs]
    aggregate_specs = STRATEGY_META.get("market_aggregate_specs", []) or []
    if isinstance(aggregate_specs, dict):
        aggregate_specs = [aggregate_specs]

    columns: list[str] = []
    for item in rank_specs:
        if isinstance(item, dict):
            col = str(item.get("feature_col", "")).strip()
            if col:
                columns.append(col)
    for item in aggregate_specs:
        if isinstance(item, dict):
            col = str(item.get("source_col", "")).strip()
            if col:
                columns.append(col)

    raw_passthrough = STRATEGY_META.get("market_feature_passthrough_cols", []) or []
    if isinstance(raw_passthrough, str):
        columns.extend(col.strip() for col in raw_passthrough.split(",") if col.strip())
    elif isinstance(raw_passthrough, (list, tuple)):
        columns.extend(str(col).strip() for col in raw_passthrough if str(col).strip())

    return list(dict.fromkeys(columns))


def _first_declared_pct_rank_col() -> str:
    rank_specs = STRATEGY_META.get("market_rank_specs", []) or []
    if isinstance(rank_specs, dict):
        rank_specs = [rank_specs]
    for item in rank_specs:
        if isinstance(item, dict):
            col = str(item.get("pct_col", "")).strip()
            if col:
                return col
    return ""


def _declared_market_aggregate_outputs() -> list[str]:
    aggregate_specs = STRATEGY_META.get("market_aggregate_specs", []) or []
    if isinstance(aggregate_specs, dict):
        aggregate_specs = [aggregate_specs]
    outputs = [
        str(item.get("output_col", "")).strip()
        for item in aggregate_specs
        if isinstance(item, dict) and str(item.get("output_col", "")).strip()
    ]
    return list(dict.fromkeys(outputs))


def _declared_market_output_columns() -> list[str]:
    rank_specs = STRATEGY_META.get("market_rank_specs", []) or []
    if isinstance(rank_specs, dict):
        rank_specs = [rank_specs]
    outputs = [
        str(item.get(name, "")).strip()
        for item in rank_specs
        if isinstance(item, dict)
        for name in ("rank_col", "pct_col", "count_col")
        if str(item.get(name, "")).strip()
    ]
    outputs.extend(_declared_market_aggregate_outputs())
    return list(dict.fromkeys(outputs))


def feature_engine(df: pd.DataFrame) -> pd.DataFrame:
    """Build stock-local time-series features for one complete panel."""
    df = df.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    grouped = df.groupby("stock_code", sort=False)
    # Input is normalized and validated at the panel boundary; no portfolio state
    # is allowed in this formula.
    # Output: time-series features and intermediate columns.
    # All rolling/shift/pct_change calls below are stock-grouped.
    # Replace the example completely if useful. Custom helpers, project APIs,
    # installed libraries, indicators, statistics, and model features are allowed.
    # Forbidden: buy_cond/sell_cond, final signal columns, cash/holding state, or future data.
    df["ret_1"] = grouped["close"].pct_change(1)
    df["momentum_20"] = grouped["close"].pct_change(20)
    df["momentum_60"] = grouped["close"].pct_change(60)
    df["ma10"] = grouped["close"].rolling(10, min_periods=10).mean().reset_index(level=0, drop=True)
    df["ma20"] = grouped["close"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    df["ma60"] = grouped["close"].rolling(60, min_periods=60).mean().reset_index(level=0, drop=True)
    df["volatility_20"] = (
        df["ret_1"].groupby(df["stock_code"], sort=False)
        .rolling(20, min_periods=20).std().reset_index(level=0, drop=True)
    )
    df["volume_ma20"] = grouped["volume"].rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
    df["volume_ratio"] = (df["volume"] / df["volume_ma20"]).replace([np.inf, -np.inf], np.nan)

    if "net_mf_amount" in df.columns:
        net_mf = pd.to_numeric(df["net_mf_amount"], errors="coerce")
        df["moneyflow_5"] = (
            net_mf.groupby(df["stock_code"], sort=False)
            .rolling(5, min_periods=5).sum().reset_index(level=0, drop=True)
        )
    else:
        df["moneyflow_5"] = 0.0
    return df


def market_feature_sources(df: pd.DataFrame) -> pd.DataFrame:
    """Build mf_* source columns for framework-generated market features.

    This hook is optional. It is used only when use_market_features=True. The
    the input is already the complete panel; the framework computes ranks or
    aggregates from aligned feature arrays after this hook returns.
    """
    # Input: complete panel, already passed through feature_engine().
    # Output: every market_rank_specs[].feature_col,
    # market_aggregate_specs[].source_col, and passthrough column declared in
    # STRATEGY_META. Prefer mf_* names.
    # Effect: the framework later computes cross-sectional ranks and market
    # breadth from aligned panel arrays.
    # Forbidden: final score, buy_cond/sell_cond, standard signal columns, or
    # ad-hoc groupby("trade_date") cross-sectional calculations; declare those
    # transforms through the market feature contract instead.
    trend_quality = (df["ma20"] / df["ma60"] - 1.0).replace([np.inf, -np.inf], np.nan)
    momentum_quality = (0.6 * df["momentum_20"] + 0.4 * df["momentum_60"]).replace(
        [np.inf, -np.inf],
        np.nan,
    )
    liquidity_quality = (df["volume_ratio"] - 1.0).clip(lower=-0.5, upper=2.0)
    volatility_penalty = df["volatility_20"].clip(lower=0.0, upper=0.15)

    df["mf_rank_score"] = (
        0.45 * momentum_quality
        + 0.35 * trend_quality
        + 0.15 * liquidity_quality
        - 0.20 * volatility_penalty
    ).replace([np.inf, -np.inf], np.nan).fillna(-1e9).astype(float)

    ret_1 = pd.to_numeric(df["ret_1"], errors="coerce")
    df["mf_is_up"] = (ret_1 > 0).where(ret_1.notna(), np.nan).astype(float)
    df["mf_is_strong"] = (ret_1 >= 0.03).where(ret_1.notna(), np.nan).astype(float)
    return df


def _compute_panel_features(df: pd.DataFrame) -> pd.DataFrame:
    df = feature_engine(df)
    df = market_feature_sources(df)

    return_cols = ["trade_date", "stock_code", *_declared_market_feature_columns()]
    missing = [col for col in return_cols if col not in df.columns]
    if missing:
        raise ValueError(
            "_compute_panel_features missing declared columns: "
            f"{missing}; check market_rank_specs/market_aggregate_specs/market_feature_passthrough_cols"
        )
    return df


def entry_exit_rules(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Create buy/sell rules; directly generate a first-class rule output."""
    # Input: panel features plus optional injected market rank/breadth cols.
    # Output: bool Series buy_cond and sell_cond aligned to df.index.
    # Semantics: buy_cond is candidate eligibility, sell_cond is exit intent.
    # These are rule outputs, not factor thresholds forced through score.
    # Rules may use any causal indicator, event/state logic, statistical method,
    # or model output; they do not need to be derived from score.
    # End with fillna(False). Do not write standard signal columns directly.
    enough_history = df["ma60"].notna()
    tradable_volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0) > 0
    trend_ok = (df["ma20"] > df["ma60"]) & (df["close"] > df["ma20"])
    momentum_ok = df["momentum_20"] > 0.03
    volatility_ok = pd.to_numeric(df["volatility_20"], errors="coerce").fillna(np.inf) < 0.12
    liquidity_ok = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(0.0) > 0.80

    rank_ok = pd.Series(True, index=df.index)
    pct_col = _first_declared_pct_rank_col()
    if bool(STRATEGY_META.get("use_market_features", False)) and pct_col and pct_col in df.columns:
        # pct_rank is usually 0..1 where smaller means better rank.
        rank_ok = pd.to_numeric(df[pct_col], errors="coerce").fillna(1.0) <= 0.35

    breadth_ok = pd.Series(True, index=df.index)
    for output_col in _declared_market_aggregate_outputs():
        if output_col in df.columns:
            breadth_ok &= pd.to_numeric(df[output_col], errors="coerce").fillna(0.0) >= 0.45

    df["entry_gate"] = (enough_history & tradable_volume & volatility_ok & liquidity_ok & rank_ok & breadth_ok)
    buy_cond = df["entry_gate"] & trend_ok & momentum_ok
    sell_cond = (df["ma20"] < df["ma60"]) | (df["momentum_20"] < -0.05) | (~volatility_ok)

    buy_cond = buy_cond.fillna(False)
    sell_cond = sell_cond.fillna(False)
    return buy_cond, sell_cond, df


def priority_score(df: pd.DataFrame, buy_cond: pd.Series, sell_cond: pd.Series) -> pd.DataFrame:
    """Create STRATEGY_META["sort_spec"] columns for same-day TopN priority."""
    _ = sell_cond
    # Input: features plus buy_cond/sell_cond from entry_exit_rules().
    # Output: numeric sortable columns referenced by STRATEGY_META["sort_spec"].
    # Semantics: score ranks candidates after buy_cond is true; it is not a signal state or weight.
    # It is also not an entry rule or target allocation.
    # Priority may be linear, nonlinear, rule-based, rank-based, or model-based;
    # it does not need to be normalized or share the entry-rule calculation.
    # Keep NaN handling explicit so non-candidates sort to the end.
    trend_score = (df["ma20"] / df["ma60"] - 1.0).replace([np.inf, -np.inf], np.nan)
    momentum_score = df["momentum_20"].clip(lower=-0.5, upper=0.8)
    liquidity_score = (df["volume_ratio"] - 1.0).clip(lower=-0.5, upper=2.0)
    volatility_penalty = df["volatility_20"].clip(lower=0.0, upper=0.15)

    raw_score = (
        0.35 * trend_score
        + 0.45 * momentum_score
        + 0.15 * liquidity_score
        - 0.20 * volatility_penalty
    )

    pct_col = _first_declared_pct_rank_col()
    if bool(STRATEGY_META.get("use_market_features", False)) and pct_col and pct_col in df.columns:
        market_rank_bonus = 1.0 - pd.to_numeric(df[pct_col], errors="coerce").fillna(1.0)
        raw_score = raw_score + 0.25 * market_rank_bonus.clip(0.0, 1.0)

    candidate_mask = pd.Series(buy_cond, index=df.index).fillna(False)
    df["score"] = raw_score.replace([np.inf, -np.inf], np.nan).fillna(-1e9).astype(float)
    df.loc[~candidate_mask, "score"] = -1e9
    return df


def _compute_panel_signals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    required = {
        "ma20",
        "ma60",
        "momentum_20",
        "volatility_20",
        "volume_ratio",
        *(
            _declared_market_output_columns()
            if bool(STRATEGY_META.get("use_market_features", False))
            else ()
        ),
    }
    missing = sorted(required - set(df.columns), key=str.casefold)
    if missing:
        raise ValueError(f"rule signal stage is missing declared feature columns: {missing}")
    buy_cond, sell_cond, df = entry_exit_rules(df)
    df = priority_score(df, buy_cond, sell_cond)
    return buy_cond, sell_cond, df


def build_index_timing_signal(index_df: pd.DataFrame) -> pd.Series:
    """Optional market-index timing hook; active only when use_index_timing=True."""
    # Input: index OHLCV sorted by trade_date. Active only when
    # STRATEGY_META["use_index_timing"] is True.
    # Output: bool Series aligned to index_df.index. The framework filters new
    # buys when False; existing positions are handled by the engine.
    ma60 = index_df["close"].rolling(60, min_periods=60).mean()
    signal = index_df["close"] >= ma60
    return signal.fillna(False)


def build_board_timing_signal(board_df: pd.DataFrame) -> pd.Series:
    """Optional board timing hook; active only when use_board_timing=True."""
    # Input: board-index OHLCV sorted by trade_date. Active only when
    # STRATEGY_META["use_board_timing"] is True.
    # Output: bool Series aligned to board_df.index. Configure board_index_codes
    # and require_board_member in STRATEGY_META to control board membership.
    ma20 = board_df["close"].rolling(20, min_periods=20).mean()
    ma60 = board_df["close"].rolling(60, min_periods=60).mean()
    signal = ma20 >= ma60
    return signal.fillna(False)


def build_time_ok(stock_code: str) -> pd.DataFrame:
    return build_index_time_ok(
        strategy_meta=STRATEGY_META,
        build_index_timing_signal=build_index_timing_signal,
        stock_code=stock_code,
    )


def build_board_time_ok(board_code: str) -> pd.DataFrame:
    return _build_board_time_ok(
        strategy_meta=STRATEGY_META,
        build_board_timing_signal=build_board_timing_signal,
        board_code=board_code,
    )


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META
    STRATEGY_SEMANTICS = STRATEGY_SEMANTICS
    STRATEGY_DESCRIPTION = STRATEGY_DESCRIPTION
    STRATEGY_LIMITS = STRATEGY_LIMITS
    def compute_features(self, panel: PanelView) -> FeatureBatch:
        return compute_feature_batch(
            panel,
            _compute_panel_features,
            strategy_meta=self.STRATEGY_META,
        )

    def compute_signals(self, panel: PanelView) -> SignalBatch:
        return compute_signal_batch(panel, _compute_panel_signals)
    build_time_ok = staticmethod(build_time_ok)
    build_board_time_ok = staticmethod(build_board_time_ok)

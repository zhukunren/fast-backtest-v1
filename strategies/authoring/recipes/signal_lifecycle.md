# Signal lifecycle

Use this recipe to decide what a strategy hook should produce and what must stay in the framework.

## Author-owned stages

1. Build causal features from information available at the signal timestamp.
2. Produce aligned boolean entry and exit conditions.
3. Produce every numeric column referenced by `sort_spec`. A buy condition means candidate eligibility; a sort value controls same-day TopN priority, not position weight.
4. Return raw entry and exit conditions through `SignalBatch`; a single strategy returns its completed frame
   from `build_single_signals(frame)`.

Return the condition Series unchanged. Do not use `.astype(bool)` or rebuild them with a replacement index before the public guardrail: arbitrary strings can become truthy and an index rebuild can hide a misaligned signal. The guardrail accepts only bool, 0/1, or null values and requires the exact working-frame index.

Panel code must stable-sort by `stock_code`/`trade_date` before time-series operations, then restore every
input key, row order, and price value before it returns. `compute_feature_batch()` and
`compute_signal_batch()` invoke the panel formula once; they do not create per-stock DataFrame slices.
The explicit `single` execution mode receives one normalized stock frame only when the strategy declares it.

## Framework-owned stages

The framework converts raw entry and exit intent into the backtest lifecycle. It owns index and board timing filters, board membership, TopN allocation, next-bar tradability, suspension and volume checks, price-limit rules, holdings, cash, costs, fills, NAV, and portfolio risk scaling.

Timing and membership block new buys. They do not instruct the strategy to liquidate an existing holding. NAV and drawdown controls scale new exposure; they do not create an exit rule.

Do not write `direction`, `buy_signal_raw`, `sell_signal_raw`, `buy_signal`, `sell_signal`, or `next_up_limit_ok`. Do not reproduce a matching engine, position state machine, or next-open calculation in a strategy module.

## Panel contract

`compute_features(PanelView)` returns a `FeatureBatch` whose arrays align one-for-one with the immutable panel.
Use `stock_id_by_row`, grouped pandas operations, or equivalent array grouping for time-series calculations.
Do not call `stock_slice()` from a formula, return a full DataFrame, or replace keys and OHLCV.

`compute_signals(PanelView)` returns a `SignalBatch` with bool `buy_raw` and `sell_raw` arrays of the same
length. The framework owns final signal columns and execution filters. A panel strategy requires a full
price-panel source; missing prerequisites are configuration errors and execution never silently switches to
the single-stock stage.

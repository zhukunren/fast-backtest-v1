# Capability selection

Select only the capabilities required by the hypothesis. The registry is the machine-readable source for hooks, metadata, dependencies, and prerequisites.

## Decision map

- Use `time_series_rules` when every feature and condition can be computed from stock-local history. Leave `use_market_features=False`.
- Use `market_rank` when a rule needs a same-date cross-sectional rank or percentile. Return one causal source feature per panel row; the framework builds rank, percentile, and count columns after the panel feature stage.
- Use `market_breadth` when a rule needs a same-date aggregate such as a ratio, sum, median, or count. Return the stock-local grouped source column; the framework performs the date aggregate.
- Use `market_passthrough` to inject extra source columns from the market-feature pass. It requires `market_rank` or `market_breadth`, because passthrough alone does not activate the market-feature layer.
- Use `index_timing` to block new buys by an index history signal.
- Use `board_membership` to restrict candidates to explicit board constituents.
- Use `board_timing` to block new buys by explicit board-index signals. It does not imply membership.
- Use `nav_risk` or `drawdown_risk` for engine-owned new-position scaling. Keep exits in the signal rules.
- Use the `panel` profile only when whole-market vectorization or cross-stock computation materially helps. Every panel scaffold implements both `compute_features` and `compute_signals`; both hooks are required by execution and cannot be excluded.

## Required wiring

When any market rank or breadth capability is selected, set `use_market_features=True` and declare the
matching specs. In the standard `rule` / `factor` path, return every declared source and passthrough column
from the complete-panel feature formula. In the expert `panel` profile, produce those aligned columns directly
in `compute_features(panel)`.

Every declared rank/aggregate source, output, and passthrough must use an author-owned derived column name.
`trade_date`, `stock_code`, OHLCV, standard signal columns, `next_up_limit_ok`, and framework-internal
`__...__` names are reserved because the market-feature attachment stage must never replace engine-owned data.
Copy a raw field into a derived name such as `mf_close_level` before ranking or aggregation.

`market_aggregate_specs[].agg` accepts `mean`, `sum`, `median`, `count`, `min`, `max`, or `std`.
Sources are coerced to numeric and pandas-style aggregates exclude `NaN`; `count` counts non-null source rows.
For a breadth ratio over every available row, emit explicit `0/1` values instead of leaving false observations
as `NaN`, otherwise the denominator becomes the date's non-null subset.

For `rule` / `factor`, the feature formula runs once before any `market_*` aggregate has been injected. Keep
that base feature formula computable without aggregate output; consume injected market breadth in entry/exit
rules or another later stage. The direct
`panel` profile instead produces sources once from the complete panel.

When board timing is enabled without membership, `board_index_codes` still cannot be empty. Membership and timing may share the same explicit board list but remain independent switches.

The timing runtimes load only their declared columns. Keep the scaffold defaults unless a timing formula needs another available source field; then update `index_timing_columns` or `board_timing_columns` together with the hook.

A declared `compute_signals()` hook is always the panel signal entry. A full price-panel source is
required; without it execution fails before signal generation.

Do not enable a switch speculatively. An enabled switch with an empty spec, placeholder hook, or absent data source is a contract error, not a no-op.

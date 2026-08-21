# Data fields and declarations

## Base daily fields

The normal daily strategy frame provides `trade_date`, `stock_code`, `open`, `high`, `low`, `close`, and `volume`. Treat types and missing values explicitly after the fixed runtime has normalized and sorted the frame.

Common optional source fields include `stock_name`, `amount`, `net_mf_amount`, `net_amount`, `net_amount_rate`, `pre_close`, and `pct_chg`. Availability depends on the configured data source. If a strategy needs an optional source column, declare it in `price_columns` so the runner loads it, and record it in `required_fields` for research auditability. Check its missing-value behavior before using it.

## Injected market fields

Injected fields exist only when `use_market_features=True` and the corresponding spec is declared:

- `market_rank_specs[].feature_col` is produced by the selected profile's complete-panel feature hook:
  grouped `market_feature_sources(df)` logic for `rule` / `factor`, or aligned `compute_features(panel)` output for `panel`.
- `rank_col`, `pct_col`, and `count_col` are framework-generated cross-sectional outputs.
- `market_aggregate_specs[].source_col` is a stock-local grouped source; `output_col` is its framework-generated date aggregate.
- `market_feature_passthrough_cols` are source fields returned by that same profile-specific hook and injected
  unchanged.

Use distinct, author-owned names for sources and outputs. `trade_date`, `stock_code`, OHLCV, standard signal
columns, `next_up_limit_ok`, and framework-internal `__...__` names are reserved in every market declaration.
A practical convention is `mf_*` for stock-local sources and `market_*` for date aggregates. Copy raw inputs into
derived `mf_*` columns; do not use the final TopN priority column as a rank source.

## Research contract

Before promotion or sealed validation, replace research-contract placeholders and record a falsifiable hypothesis, exact input dependencies, universe assumptions, expected regimes, and non-overlapping in-sample and out-of-sample periods. Draft backtests may run before this declaration is complete.

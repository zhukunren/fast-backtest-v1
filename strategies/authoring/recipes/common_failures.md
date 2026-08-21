# Common authoring failures

1. Future information: do not use negative shifts, negative `pct_change` periods, centered rolling windows, or t+1 prices in a t-day signal.
2. Unsorted history: sort by date before time-series operations; panel code must group by stock and restore the original key order.
3. Hand-written standard signals: return aligned conditions through `compute_signal_batch()` or return an aligned `SignalBatch`; never write framework signal columns.
4. Cross-sectional work inside a stock-local transform: use market feature declarations or the panel arrays.
5. Missing ranking output: every `sort_spec` column must exist, be numeric, and have an intentional missing-value policy.
6. Hot-path side effects: do not launch commands, read global configuration, write external files, or fetch remote data from strategy hooks.

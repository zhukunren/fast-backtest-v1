# Engine-owned risk controls

Use these capabilities only when the hypothesis requires portfolio NAV or drawdown scaling. The engine owns
the state machine and exposure application; the strategy declares thresholds and ratios and keeps its own
entry/exit logic separate.

## NAV scaling

- Threshold order: `0 <= nv_clear <= nv_danger <= nv_warn`.
- `nv_ratios` contains exactly four finite, non-negative values in
  `(normal, warning, danger, clear)` state order.
- The state becomes warning below `nv_warn`, danger below `nv_danger`, and clear below `nv_clear`.

## Drawdown scaling

- Drawdown values are zero or negative. Threshold order:
  `lock_dd <= danger_dd <= warn_dd <= 0`.
- `dd_ratios` contains exactly four finite, non-negative values in
  `(normal, warning, danger, lock)` state order.
- The state becomes warning at or below `warn_dd`, danger at or below `danger_dd`, and lock at or below
  `lock_dd`.

When both controls are enabled, the stricter (smaller) active ratio wins. Ratios are exposure multipliers,
not signal thresholds or exit conditions. Validate the resulting state columns and exposure path in artifacts;
do not reproduce this state machine inside a strategy module.

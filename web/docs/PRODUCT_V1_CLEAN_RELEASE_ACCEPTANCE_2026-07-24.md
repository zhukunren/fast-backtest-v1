# Product V1 Clean release acceptance

Status: completed for Web product V1 clean release at
`82817a77f8b8a3ecf6b310732ca99efcfee9a28e`.

This contract closes the Web strategy-library product cutover without weakening
the repository's historical five-engine formal D4 protocol. The formal protocol
remains available for a future general-purpose CLI release.

## Supported release surface

- Web strategy library and its read/run/compare APIs.
- `legacy` and `slot_broker`, the only engines selected by the 14 active Web
  strategy configurations at contract freeze time.
- Schema-v2 `RunResult` and atomic Web cache generations.
- Slot cash ownership, pending-sell, partial-fill, order/fill, and holdings
  lifecycle behavior.

`slot`, `broker`, and `fixed_rebalance` remain covered by contract/unit tests and
composition smoke tests. They are not part of this Web product's real-data
release campaign because the Web API does not allow callers to override a
strategy's configured engine.

## Product D4 gates

The candidate must be a clean, identity-stable Git worktree. Runtime changes
after evidence collection invalidate the evidence.

1. The full repository quality gate, including Web tests/build, must pass.
2. Slot cash regression tests must pass.
3. All 14 active Web strategies must be rebuilt from the candidate and pass the
   strict strategy-library health check.
4. Real-data A/B probes must cover `legacy` and `slot_broker` for both
   `codex_2024_2026_qualified_reclaim_chase` and
   `codex_chase_dd_control_breakout`.
5. A/B semantic comparison, input identity, and within-release determinism are
   hard gates. Historical relative timing confidence intervals are diagnostic.
6. For each candidate engine/strategy probe, median runtime must not exceed
   5.0 seconds and no sample may exceed 1 GiB peak process-tree RSS.
7. The fixed non-empty TopN slice and the 14-strategy cache report must be bound
   into the evidence set with SHA-256 hashes.
8. A real old-Web release drill must run in an isolated worktree, on port 15119,
   with its own strategy cache. It must not change the current release's cache
   manifest and the current service must remain healthy on port 5119.

## Product D5 gates

1. Record one evidence summary bound to the final candidate commit, panel hash,
   configuration hash, cache report hash, and rollback report hash.
2. Create an annotated release tag only after all Product D4 gates pass.
3. Keep the archived old release branch/tag and the external artifact archive as
   the recovery source.
4. Publish release and rollback notes, then verify the live local service and
   removed `/admin` boundary after tagging.
5. Update the architecture status to distinguish this completed Web product
   release from the still-available historical five-engine formal protocol.

## Evidence locations

Release evidence is generated outside tracked source files under
`output/tools/product_v1_clean/` or a release-specific directory under `D:/tmp`.
Tracked source contains the contract and final release note, not bulky benchmark
or cache artifacts.

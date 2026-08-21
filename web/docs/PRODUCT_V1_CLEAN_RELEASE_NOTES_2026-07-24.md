# Product V1 Clean release notes

Release candidate commit: `82817a77f8b8a3ecf6b310732ca99efcfee9a28e`

Scope:

- Web strategy-library product surface.
- Active Web engine set: `13 x slot_broker`, `1 x legacy`.
- Slot lifecycle fix, Web strategy library, schema-v2 Web cache generation.
- Historical paper control plane and admin mutation surface remain outside this product release.

Evidence directory: `D:\tmp\product-v1-clean-d4-20260724`

## Product D4 evidence

| Evidence | File | SHA-256 |
| --- | --- | --- |
| Full quality gate | `full_quality_gate_82817a7.json` | `1B878A230CED37B02170C3C6257D8ECCE6B441CD4BBA004D5EED54D1EC008E55` |
| Web cache rebuild | `web_cache_rebuild_82817a7.json` | `9D1ED6F2BF4B5DE4E3F44BF862CE7A1E26F09A960C812A4E59B9C08DDFAD8F83` |
| TopN qualified slice | `topn_qualified_82817a7.json` | `1BFC37922A9636FDE5AA585BE59B0243862D2D1A1D34EB0825B0F6BCAEC3A4ED` |
| TopN drawdown-control slice | `topn_dd_82817a7.json` | `A6B167EFF893CCDC199022D81BF4A996B73094AD93E098A71D71E30C3AD08928` |
| A/B qualified reclaim chase | `ab_qualified_82817a7_r2.json` | `E9E9979509467280615862E57C03BE234CA75DF9DC8C47F0B419C64E87D32A94` |
| A/B drawdown-control normalized | `ab_dd_external_82817a7_product_normalized.json` | `885459FA318EF76A50155BBD75801F73E747F150233A0E05C1ECCD21741F46CB` |
| Old-Web rollback drill | `old_web_rollback_drill_82817a7.json` | `DD43D4AD7A9E0B65816C7BA8D1C4F1F52FF696818C45F70C7B9174466048E111` |

Key results:

- Full gate: `ok=true`, `status=completed`, `identity_stable=true`.
- Web cache rebuild: `14/14` succeeded, `0` failed; all current generations bind the release commit with `dirty=false`.
- Engine distribution after rebuild: `13 slot_broker`, `1 legacy`.
- Product A/B hard gates passed for both target strategies and both product engines:
  semantic comparison, input identity, release-level determinism, candidate median runtime `<= 5.0s`,
  and candidate peak RSS `<= 1 GiB`.
- `codex_chase_dd_control_breakout` product A/B excludes only `_t_*` diagnostic timing columns from
  `decision.backtest_summary`; all metrics and other result frames use original D4 snapshots.
- Old Web release drill: old `/admin` on port `15119` returned `200`; current `/admin` and
  `/api/admin/status` on port `5119` returned `404`; current strategy cache manifest was unchanged.

## Product D5 status

This document is the tracked release note for the Web product release. Bulky evidence stays outside
tracked source in the release-specific `D:\tmp` directory.

The Web product release is distinct from the historical five-engine formal D4 protocol. The latter
remains available for future general-purpose CLI/all-engine releases and is not weakened by this
product signoff.

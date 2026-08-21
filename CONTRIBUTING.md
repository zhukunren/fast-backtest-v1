# Contributing

This repository maintains one implementation: the complete V1 workspace.
Changes must preserve its CLI, Web, data, strategy-authoring, execution, and
full-public export contracts. Do not introduce a second package or parallel
implementation surface.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python tools\verify_changed.py
python tools\verify_changed.py --execute
```

本地默认验证由 `tools/verify_changed.py` 按 `config/change-impact.json` 选择；完整 CI/full gate 不作为每次
修改的默认本地步骤。authoring、性能敏感路径和发布候选仍按规划器或 release 协议执行专项 gate。

For a public V1 source release, first run the commit-bound full gate, then
export only tracked, reviewed files and run the candidate checks from the
exported checkout:

```powershell
$fullGateSha = git rev-parse HEAD
$fullGateReport = "output/tools/full_quality_gate/full-$fullGateSha.json"
python tools\full_quality_gate.py --profile full --include-web-build --include-topn-slice --json-out $fullGateReport
python tools\validate_full_gate_report.py $fullGateReport --expected-profile full --expected-commit $fullGateSha --require-web-build --require-topn-slice
python tools\export_full_repo.py --clean
python tools\audit_full_public_surface.py --root .tmp\jshs\public-repos\full_public_repo
Set-Location .tmp\jshs\public-repos\full_public_repo
python tools\smoke_full_public_repo.py
```

Publish the exported directory as a new repository only after a separate
history-wide secret scan, license review, and clean-commit review. Do not
include vendor data, local credentials, runtime caches, generated research
strategies, or `libreoffice1.cab`.

For ordinary changes, start with the change-aware planner:

```powershell
python tools\verify_changed.py
python tools\verify_changed.py --execute
```

The planner selects offline, quick performance, hot-path, authoring, Web, and
documentation checks from `config/change-impact.json`. The quick performance
checks are diagnostic only; run the canonical `perf_smoke.py` /
`hotpath_smoke.py` without `--quick` when producing release evidence.

## Workspace Rules

- Product code uses the workspace-owned contracts under `app`, `backtest`,
  `data`, `strategies`, `trading`, and `web`; do not duplicate them under a
  packaging-only root.
- The curated public candidate must pass its full-surface audit and smoke from
  inside the exported checkout.
- Public candidates contain no credentials, vendor data, runtime output,
  generated research strategies, private configuration, or internal Git
  history.
- Offline fixtures must stay synthetic, deterministic, sorted, and complete.
- User-visible behavior requires tests at the owning layer; architecture or
  framework changes also require the repository gates in `AGENTS.md`.
- Full-public exclusions and inputs are owned by `tools/export_full_repo.py`;
  do not maintain a second exporter, CI workflow, or release checklist.

## Pull Request Checklist

- Add or update tests for user-visible behavior changes.
- Update `CHANGELOG.md` for user-visible workspace changes.
- Keep sample data synthetic or clearly redistributable.
- Run the relevant verification commands and include them in the PR.

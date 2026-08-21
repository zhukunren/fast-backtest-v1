# Full Edition Release Candidate

Use this protocol before publishing or handing off a full workspace candidate.
A formal candidate is a structured report bound to one clean Git commit. Console
output, a dirty-worktree run, or an unvalidated `latest.json` file is diagnostic
only.

## Preconditions

Install the locked Python and frontend dependencies, and make the fixed TopN
input available for `2025-05-16`. Then confirm that the candidate is committed
and the worktree is clean:

```powershell
git status --short
$fullGateSha = git rev-parse HEAD
$fullGateReport = "output/tools/full_quality_gate/full-$fullGateSha.json"
```

`git status --short` must print nothing. Do not edit, generate tracked files, or
switch commits while the gate is running.

## Produce Evidence

```powershell
python tools\audit_full_release.py
python tools\full_quality_gate.py --profile full --include-web-build --include-topn-slice --json-out $fullGateReport
```

The full profile must retain both required options. `--include-web-build` runs
the frontend tests, both coverage gates, type check, production build, and
bundle audit. `--include-topn-slice` verifies the resolved source and records
the fixed TopN result plus a portable `repo://` input reference, input SHA256,
size, row count, and preview. Performance and
hotpath gates are mandatory; `--skip-perf` and `--skip-hotpath` invalidate a
formal candidate.

Use a commit-specific JSON path as shown above. Do not promote a mutable
`latest.json` or console transcript.

## Validate Evidence

```powershell
python tools\validate_full_gate_report.py $fullGateReport --expected-profile full --expected-commit $fullGateSha --require-web-build --require-topn-slice
git rev-parse HEAD
git status --short
```

The validator must return zero, the final SHA must still equal `$fullGateSha`,
and the final status output must remain empty. The validator rejects a failed or
incomplete canonical step list, a dirty start/end identity, an identity change
during the run, a mismatched full commit SHA, missing Web/TopN evidence, and
non-canonical report names.

Formal reports use portable schema v2: Python is bound by executable SHA256 and
size, gate temporary files use `gate-tmp://`, and host-specific absolute paths
are rejected recursively before promotion.

Store or publish the exact validated JSON together with artifacts from the same
commit. Never attach a report to a different rebuild or move an existing release
tag to make identities appear to match.

The local validator checks schema, command, result, and Git-identity consistency;
it is not a cryptographic signature and cannot prove who produced a JSON file.
External promotion should retain the trusted CI run and artifact provenance, or
an independently signed attestation, alongside the validated report.

## Diagnostic Runs

Faster profiles are useful before committing, but cannot become release
evidence:

```powershell
python tools\full_quality_gate.py --profile ci --skip-hotpath
```

## Additional Real-Data Evidence

Long-window validation remains separate evidence and does not replace the
required fixed TopN step in the full-gate report:

```powershell
python tools\run_long_window_validation.py --case mysql_real --start-date 20240101 --end-date 20251231 --stock-limit 500 --json-out output\tools\long_window_validation\full-$fullGateSha.json
```

## Do Not Include

- `output/`
- `dist/`
- `dist_public/`
- `build/`
- `data/panel_cache/`
- `web/data/`
- `web/user_workspaces/`
- Web strategy runtime caches or `run_meta`
- `config/config.local.ini`
- database dumps or vendor data

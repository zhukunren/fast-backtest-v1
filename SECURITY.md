# Security Policy

## Supported Versions

Security fixes are applied to the latest complete V1 workspace release. The
curated full-public source export is the only supported public distribution.

## Reporting A Vulnerability

Do not open a public issue for credential leaks, arbitrary code execution,
path traversal, unsafe deserialization, or dependency compromise.

Report privately through a GitHub Security Advisory (the repository's
**Security** tab) when that feature is enabled. If advisories are unavailable,
contact the repository maintainer using the contact listed in the repository
hosting profile. Do not include live credentials in the report; revoke them
first and attach only a redacted reproduction.

Include:

- A short description of the issue.
- A minimal reproduction or affected file path.
- Any known exploit preconditions.
- Whether credentials, private data, or generated artifacts are involved.

The maintainer should acknowledge critical reports within 48 hours when the
project is public. Until then, treat this file as the disclosure process for
private pre-release review.

## Scope

In scope:

- The V1 CLI, engine, data adapters, strategy contracts, and full-public export.
- Offline fixtures and release metadata included in the curated source export.
- Full-workspace Web authentication, sensitive maintenance endpoints, strategy
  upload boundaries, and dependency compromise reports. Uploaded strategies
  are trusted administrator code, not sandboxed content; reports that cross
  this documented trust boundary are still in scope when they bypass auth or
  affect other tenants/data unexpectedly.

Before promoting a public release candidate, run:

```powershell
python tools\export_full_repo.py --clean
python tools\audit_full_public_surface.py --root .tmp\jshs\public-repos\full_public_repo
Set-Location .tmp\jshs\public-repos\full_public_repo
python tools\smoke_full_public_repo.py
```

Out of scope for public security support:

- Local private configs such as `config/config.local.ini`.
- Ignored output directories.
- Research records, generated strategy experiments, and independent V2/V3
  projects excluded from the curated public source export.

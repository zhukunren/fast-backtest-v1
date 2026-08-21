# Changelog

This file tracks user-visible changes to the complete V1 workspace, the
project's only maintained implementation and public source surface. History for
retired distribution experiments remains available in Git history.

## [Unreleased]

### Changed

- Consolidated public delivery on the curated complete V1 source export.
- Made `tools/export_full_repo.py`, `tools/audit_full_public_surface.py`, and
  `tools/smoke_full_public_repo.py` the single public release chain.
- Made public exports read committed `HEAD` content so uncommitted workspace
  changes cannot contaminate a release candidate.
- Strategy descriptions displayed by Web are now source-bound LLM-authored
  manifests; Web validates and renders them without compiling source semantics.

### Removed

- Removed the duplicate package implementation and its examples,
  package-only documentation, CI, artifact publishing, and compatibility
  tooling.
- Removed package archive and remote-index release workflows. Public delivery
  is source-based and contains the complete V1 product surface.

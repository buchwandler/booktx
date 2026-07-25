---
schema_version: 4
id: content-0015
kind: content
type: stakeholder
title: Project Maintainers
status: accepted
section: introduction_and_goals
order: 30
version: 3
contact: ""
expectations:
  - Reproducible builds
  - Auditable version history
  - Profile isolation
  - Quality gate enforcement
body_format: markdown
---

Project maintainers rely on the quality gate (scripts/quality_gate.py) for release readiness, the version ledger for provenance, profile isolation for multi-target projects, and deterministic builds for reproducibility. Key concerns: CI enforcement, mypy strict compliance, Ruff linting, and full test coverage.

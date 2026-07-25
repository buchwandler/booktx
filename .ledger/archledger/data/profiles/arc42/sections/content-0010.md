---
schema_version: 4
id: content-0010
kind: content
type: section
section: quality_requirements
title: Quality Requirements
order: 100
status: accepted
version: 2
body_format: markdown
---

## Quality Requirements

### QR-1: Deterministic Builds

Given identical source, context, and profile config, `booktx build` must produce byte-for-byte identical output. All hashing is SHA-256; all JSON serialization is deterministic with `sort_keys=True`.

**Measurement:** Run build twice with same inputs, compare SHA-256 of output files.

### QR-2: Profile Isolation Guarantee

No command operating on profile A may read or write profile B's state. Cross-profile operations (`profile compare`, `judge`) must explicitly declare all participating profiles.

**Measurement:** Code review confirms all store/context paths derive from a single `Project.profile_dir`.

### QR-3: Agent Submission Safety

A malformed or malicious submission must never corrupt the canonical store. Submissions are validated against task snapshots before any store mutation occurs.

**Measurement:** Test suite covers invalid submissions (wrong IDs, missing records, tampered context hashes).

### QR-4: Startup Robustness

`booktx` CLI must produce exit code 70 on import failure, render actionable diagnostics, and never modify project data during startup.

**Measurement:** `test_bootstrap.py` verifies exit codes, message format, and `BOOKTX_DEBUG` behavior.

### QR-5: Test Coverage

All command workflows, store operations, validation rules, and data models must have test coverage. Quality gate enforces full test suite pass before release.

**Measurement:** `python -m pytest -q` must pass; `scripts/quality_gate.py` must exit 0.

### QR-6: Type Safety

All public interfaces must be fully type-annotated. `mypy --strict` must pass on the `booktx` package.

**Measurement:** `python -m mypy booktx` exits 0.

### QR-7: Lint Compliance

Code must pass Ruff linting with project configuration (`.ruff.toml`).

**Measurement:** `python -m ruff check .` exits 0.

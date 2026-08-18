---
schema_version: 4
id: content-0011
kind: content
type: section
section: risks_and_technical_debt
title: Risks and Technical Debt
order: 110
status: accepted
version: 3
body_format: markdown
---

## Risks

### RISK-1: V2 Compatibility Materialization Scalability

**Severity:** Medium | **Probability:** Medium

Compatibility operations that materialize v2 state still load the full accepted store. Large books (100k+ records) therefore produce multi-megabyte `translation-store.json` compatibility snapshots or equivalently large in-memory materializations.

**Mitigation:** V3 is the new-profile default, uses bounded per-chunk writes, shared reader revisions, recovery journals, doctor inventory, and explicit v2↔v3 migration/rollback. Parity and readiness-gate tests validate the two backends.

### RISK-2: Agent Context Window Overflow

**Severity:** Medium | **Probability:** Medium

Large chapters with many context records may exceed LLM context windows. Task word budgets and context window sizes are configurable but not automatically enforced against external model limits.

**Mitigation:** `batch_words` and `before_records`/`after_records` config options. `include_untranslated_neighbors` toggle for review tasks. Todo `max_run_words` cap.

### RISK-3: Placeholder Collision

**Severity:** Low | **Probability:** Low

If source text contains literal `__NAME_NNN__`-like strings, extraction may produce false placeholder matches.

**Mitigation:** Placeholder tokens use a distinct pattern unlikely in natural text. Custom patterns configurable via `SourceAnalysisPatternsConfig`.

### RISK-4: Source Format Drift

**Severity:** Medium | **Probability:** Low

EPUB or Markdown parsing libraries may change behavior across versions, affecting extraction output.

**Mitigation:** Pinned dependency versions in `pyproject.toml`. Manifest records source SHA-256; extraction checks for source drift. Test suite covers format-specific edge cases.

## Technical Debt

| Item                                                | Impact                                                                     | Remediation                                    |
| --------------------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------------- |
| Legacy layout support (`config.py` dual code paths) | Maintenance burden; every path resolver has two branches                   | Deprecate legacy layout after migration window |
| V1 flat store compatibility surface                 | `legacy_store_to_v2()` and `migrate_legacy_store()` kept for import/export | Remove after V2-only guarantee                 |
| `chapter-map.json` dual location                    | Legacy and profile layouts store it differently                            | Normalize to `.booktx/chapter-map.json`        |
| `context_booktx.*` generated files in repo root     | 6+ MB of context analysis artifacts                                        | Move to `.booktx/` or `.gitignore`             |

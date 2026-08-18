---
schema_version: 4
id: content-0009
kind: content
type: section
section: architecture_decisions
title: Architecture Decisions
order: 90
status: accepted
version: 3
body_format: markdown
---

## Architecture Decision Records

### ADR-1: V3 Default Store with Explicit Compatibility Boundaries

**Status:** Accepted

**Context:** The original V1 flat store (`TranslationStore`) was a simple dict of record IDs to targets. V2 nested candidate and review provenance into `StoredTranslationRecordV2`, but large books and bounded workflows now require a shard-based canonical backend without dropping compatibility tooling.

**Decision:** New profiles default to the v3 `translation-store/` canonical backend. Existing profiles keep their detected v1/v2/v3 backend until explicit migration or rollback. `TranslationStoreV2` remains the supported compatibility materialization model and legacy backend for migration, rollback, parity, and portable judge snapshots rather than the universal application-domain store.

**Consequences:**

- (+) Ordinary workflows can use bounded chunk reads and writes against the canonical repository
- (+) Existing profiles retain explicit migration and rollback paths
- (-) Full-store compatibility materialization still exists at migration, doctor, rollback, and snapshot boundaries and must stay isolated from ordinary workflow code
- (-) Documentation and tests must distinguish the logical translation store from any one physical backend

### ADR-2: Typer with Command Catalog Fallback

**Status:** Accepted

**Context:** Typer provides native help and command registration. But command metadata (summaries, panel groupings, deprecated commands) must not block CLI startup if malformed.

**Decision:** `command_catalog.py` defines `SUMMARY_OVERRIDES` and panel metadata as typed dicts. `apply_command_catalog()` applies them at startup. If values are invalid (e.g., a tuple instead of a string), a warning is emitted and native Typer help is used. Strict validation exists separately for tests and release tooling.

**Consequences:**

- (+) CLI never fails to start due to cosmetic metadata issues
- (+) Strict checks catch regressions before release
- (-) Dual validation paths must stay synchronized

### ADR-3: Lazy Bootstrap Entry Point

**Status:** Accepted

**Context:** A typo in `command_catalog.py` (`SUMMARY_OVERRIDES` value as tuple instead of string) caused `TypeError` during CLI import, producing an unfriendly traceback.

**Decision:** `pyproject.toml` console script points to `booktx.bootstrap:main`, which wraps `booktx.cli:main` in a try/except. Import failures render concise diagnostics with exit code 70, project-data safety message, and `BOOKTX_DEBUG=1` opt-in.

**Consequences:**

- (+) Users get actionable error messages instead of raw tracebacks
- (+) Project data is never modified on startup failure
- (-) Adds one extra module in the import chain

### ADR-4: Review Pass Order as Lexicographic DAG

**Status:** Accepted

**Context:** Multi-pass reviews need a clear ordering constraint. A review based on another review should only move forward.

**Decision:** Review refs use `R<pass>.<run>` format. The total order is lexicographic on `(pass_number, run_number)`. A review based on another review must satisfy `(new.pass, new.run) > (base.pass, base.run)`. This permits same-pass reruns (`R1.2` from `R1.1`) and higher-pass reviews from lower-pass (`R2.1` from `R1.2`), while rejecting cycles and regression.

**Consequences:**

- (+) Clear, enforceable ordering
- (+) Cycle detection via graph walk in `_validate_review_graph_is_acyclic()`
- (-) Review consumers must understand the lexicographic constraint

### ADR-5: Profile Root Markers for Agent Isolation

**Status:** Accepted

**Context:** Coding agents need unambiguous project/profile resolution without guessing. Running `booktx` from a profile root should "just work."

**Decision:** `write_profile_root_marker()` writes `.booktx-profile.json` containing profile name, source identity, and target locale. A command run from a profile root resolves the profile from this marker and treats `.` as the project root. No implicit single-profile resolution exists.

**Consequences:**

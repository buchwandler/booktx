---
schema_version: 4
id: content-0008
kind: content
type: section
section: cross_cutting_concepts
title: Cross-cutting Concepts
order: 80
status: accepted
version: 2
body_format: markdown
---

## Cross-Cutting Concepts

### Placeholder Protection

During extraction, booktx replaces non-translatable spans (proper names, HTML/XML tags) with deterministic tokens (`__NAME_001__`, `__TAG_001__`). The `Placeholder` model records the token-to-original mapping. The translation agent sees only placeholdered text; the build step restores originals verbatim. This guarantees that markup and protected names survive round-trip without agent awareness.

### Content-Addressed Hashing

Every config, context view, glossary binding, and task snapshot is SHA-256 hashed. These hashes travel with task records, version candidates, and review candidates. Validation compares stored hashes against current filesystem state to detect drift. The pattern enables:

- Staleness detection (`source_sha256` mismatch triggers re-extraction warning)
- Context drift detection (task context hash vs. current context hash)
- Review chain staleness (base target hash vs. current base target)

### Version Provenance

The `TranslationVersionLedger` records who produced each major version (actor, harness, model) and each subversion's context hash. The `TranslationCandidate` and `TranslationReviewCandidate` models carry baseline refs, context view hashes, and config hashes. This creates a complete provenance trail from source to output.

### Strict vs. Lazy Validation

booktx distinguishes two validation modes:

- **Runtime/startup**: Malformed optional metadata emits one warning and falls back; the CLI must never refuse to start due to cosmetic metadata issues
- **Strict (tests/release)**: `command_catalog.py` strict validation and `scripts/quality_gate.py` catch all issues before release

### Profile Isolation

Profiles are hard-isolated leaf directories. A build or validation run resolves exactly one profile. The only cross-profile operations are explicit comparison commands (`profile compare`, `judge`). No profile reads another profile's store during normal operations.

### Agent Protocol

The agent interaction follows a strict contract:

1. Agent requests next task → receives immutable JSON with frozen context
2. Agent processes task against the snapshot (never mutates files directly)
3. Agent submits results → booktx validates and applies to canonical store
4. `AgentNextAction` model signals `continue`, `complete`, or `blocked` with safe-next-command hint

### Todo Lifecycle

`TranslationTodo` and `ReviewTodo` are durable run-control artifacts for bounded multi-chapter/-pass agent runs. They carry scope, stop conditions, starting totals, and chapter lists. A companion `TranslationTodoLifecycle` tracks mutable state (open/completed/abandoned/superseded) separately from the immutable todo.

### Linguistic Auditing

Optional submission-time checks: placeholder integrity, suspicious length ratios, and target-language rule validation. Configured via `SubmissionQualityConfig` in profile config. Findings are non-blocking warnings by default.

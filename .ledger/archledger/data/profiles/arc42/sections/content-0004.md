---
schema_version: 4
id: content-0004
kind: content
type: section
section: solution_strategy
title: Solution Strategy
order: 40
status: accepted
version: 2
body_format: markdown
---

## Core Design Decisions

### Source-First, Profile-Isolated Architecture

The project root holds a single source document and a shared `.booktx/` directory containing extracted source state (chunks, manifest, chapter map, protected names). Every translation target lives in its own `translations/<profile>/` directory with its own store, context, version ledger, tasks, and output. This enforces hard isolation: no profile ever reads another profile's mutable state.

### Content-Addressed Immutability

Every meaningful artifact is hashed with SHA-256: source files, context snapshots, profile configs, glossary bindings, task context views. These hashes are stored in task records and version candidates so provenance is always verifiable. Tasks carry the exact context hash at creation time; task context views are immutable snapshots under `context-history/views/<sha>/`.

### Task Snapshot Pattern

When an agent requests a translation task (`translate next`), booktx snapshots all relevant state (context view, glossary bindings, termbase entries, config hashes) into an immutable task record. The agent works against this frozen snapshot, not mutable files. Submissions are validated against the snapshot and only then applied to the canonical store.

### Versioning Model

Translation versions use `major.minor` refs (e.g., `1.1`, `2.3`). Major versions represent identity/harness/model changes tracked in the `TranslationVersionLedger`. Minor (subversion) bumps occur when context or baseline policy changes. Review candidates use `R<pass>.<run>` refs and form a derivation DAG rooted at a translation version.

### Technology Stack Rationale

| Choice                | Why                                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------------------- |
| Typer + Rich          | Mature CLI framework with good help rendering; Rich provides structured console output                    |
| Pydantic v2           | Strict validation at all JSON boundaries; `model_validate_json` for ingress, `model_dump_json` for egress |
| markdown-it-py        | CommonMark-compliant Markdown parsing with plugin support                                                 |
| BeautifulSoup 4       | EPUB XHTML parsing and manipulation                                                                       |
| Hatchling + hatch-vcs | PEP 621 build with VCS-based version inference                                                            |
| No async              | File I/O is inherently synchronous; async adds complexity without benefit                                 |

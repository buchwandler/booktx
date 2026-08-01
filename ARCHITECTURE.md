---
title: "Architecture Documentation"
version: 2
generator: "archledger 0.4.0"
arc42_template_version: "9.0-EN"
---

# Architecture Documentation

Generated from archledger records. Do not edit this generated file directly.

# Introduction and Goals

## Overview

booktx is a deterministic, source-first CLI tool that prepares Markdown and EPUB documents for translation by a coding agent. It enforces a strict lifecycle: extract structured source records from a document, manage translation profiles with per-profile isolated state, generate version-tracked tasks for translators, validate submissions, review and judge output quality, and build translated output artifacts.

## Stakeholders

| Role                 | Concern                                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| Human translators    | Clear, scoped tasks with context and glossary binding; safe submissions that do not corrupt profile state |
| Coding agents (LLMs) | Deterministic JSON contracts, immutable task context snapshots, explicit state-transition rules           |
| Project maintainers  | Reproducible builds, auditable version history, profile isolation, quality gates                          |
| Tool integrators     | Stable CLI interface, documented data models, machine-readable status output (`--json`)                   |

## Quality Goals

| #    | Goal                              | Motivation                                                                                                            |
| ---- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| QG-1 | **Profile isolation**             | One profile's translation/judge/review state must never read or mutate another profile's state                        |
| QG-2 | **Deterministic reproducibility** | Given the same source, context, and profile config, builds produce identical output; all hashes are content-addressed |
| QG-3 | **Agent-safe boundaries**         | Agents operate on task snapshots, not mutable files; submissions are validated before store mutation                  |
| QG-4 | **Lazy startup robustness**       | CLI import failures produce concise diagnostics, exit code 70, and never corrupt project data                         |
| QG-5 | **Provenance auditability**       | Every record carries version, baseline, context, and config hashes; review chains are verifiable DAGs                 |

## Requirements Overview

<!-- archledger: no accepted records for this section yet -->

## Quality Goals

| Title                         | Priority | Scenario                                                                                                        |
| ----------------------------- | -------- | --------------------------------------------------------------------------------------------------------------- |
| Profile Isolation             | 1        | A build for profile de_default never accesses translations/fr_default/ state                                    |
| Deterministic Reproducibility | 2        | Two builds with identical inputs produce SHA-256 identical output files                                         |
| Agent-Safe Boundaries         | 3        | An agent submits a translation with tampered context hash; booktx rejects without mutating the store            |
| Lazy Startup Robustness       | 4        | A syntax error in command_catalog.py causes import failure; booktx renders exit code 70 with actionable message |
| Provenance Auditability       | 5        | Stale review chain is detected when base target hash differs from stored base_target_sha256                     |

## Stakeholders

| Title                | Contact | Expectations                                                                                                                                   |
| -------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Human Translators    |         | Clear task boundaries, Safe submissions that do not corrupt profile state, Transparent acceptance/rejection                                    |
| Coding Agents (LLMs) |         | Deterministic JSON contracts, Immutable task context snapshots, Explicit state-transition rules, No direct file mutation outside task contract |
| Project Maintainers  |         | Reproducible builds, Auditable version history, Profile isolation, Quality gate enforcement                                                    |
| Tool Integrators     |         | Stable CLI interface, Documented data models, Machine-readable --json output                                                                   |

# Architecture Constraints

## Technical Constraints

| #   | Constraint                         | Rationale                                                                                                                   |
| --- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| C-1 | **Python >= 3.10**                 | Must support the oldest non-EOL Python with full typing; no Python 3.9 compatibility required                               |
| C-2 | **Single-file translation stores** | `TranslationStoreV2` is a single JSON file per profile; V3 shard-based store is an opt-in migration target                  |
| C-3 | **Typer CLI framework**            | All commands registered via Typer; command catalog provides optional metadata without blocking startup                      |
| C-4 | **Pydantic >= 2 data models**      | All JSON boundaries use Pydantic models with strict `extra="forbid"` or `extra="allow"` for forward-compatibility detection |
| C-5 | **Source-first architecture**      | Shared `.booktx/` holds extracted source state; profiles are leaf directories under `translations/`                         |
| C-6 | **No database**                    | All state is filesystem-based (JSON, TOML, Markdown); no SQL or external services                                           |

## Organizational Constraints

| #   | Constraint                                                                        |
| --- | --------------------------------------------------------------------------------- |
| O-1 | MIT-licensed; must remain installable via pip without proprietary dependencies    |
| O-2 | CLI is the only supported interface; no REST API, GUI, or plugin system           |
| O-3 | Commit history follows Conventional Commits                                       |
| O-4 | Release artifacts published to PyPI via GitHub Actions with exact commit evidence |

## Conventions

| #    | Convention                                                                                                           |
| ---- | -------------------------------------------------------------------------------------------------------------------- |
| CV-1 | Protected names (`names.json`) are manually curated and bound into extraction                                        |
| CV-2 | Chapter segmentation follows source document heading hierarchy; `chapter-map.json` captures chapter-to-chunk mapping |
| CV-3 | Placeholder tokens use `__NAME_NNN__` and `__TAG_NNN__` patterns; they survive round-trip through translation        |
| CV-4 | Record IDs follow `chunk_id-part_id` (e.g., `0001-000042`); canonical via `canonical_record_id()`                    |

- **Python >= 3.10**
  - Impact: Must support the oldest non-EOL Python with full typing support
  - Notes: Describe the rationale and consequences of this constraint.
- **Detected translation stores**
  - Impact: New profiles use v3 per-chunk shards; existing profiles retain detected v1/v2/v3 storage, with TranslationStoreV2 as the compatibility model
  - Notes: Describe the rationale and consequences of this constraint.
- **Typer CLI Framework**
  - Impact: All commands registered via Typer; command catalog provides optional metadata
  - Notes: Describe the rationale and consequences of this constraint.
- **Pydantic >= 2 Data Models**
  - Impact: All JSON boundaries use Pydantic models with strict validation
  - Notes: Describe the rationale and consequences of this constraint.
- **Source-First Architecture**
  - Impact: Shared .booktx/ holds extracted source state; profiles are isolated leaf directories
  - Notes: Describe the rationale and consequences of this constraint.
- **No Database — Filesystem Only**
  - Impact: All state is filesystem-based JSON/TOML/Markdown; no SQL or external services
  - Notes: Describe the rationale and consequences of this constraint.
- **MIT License**
  - Impact: Must remain pip-installable without proprietary dependencies
  - Notes: Describe the rationale and consequences of this constraint.

# Context and Scope

## System Context

booktx operates as a filesystem-level CLI tool with three external touchpoints:

1. **Source documents** (Markdown `.md` or EPUB `.epub`) — read once during extraction, treated as immutable reference
2. **Coding agents / translators** — consume task JSON, produce submission JSON; interact only through the task/submission contract
3. **Version control (git)** — tracks every state change; booktx itself is VCS-agnostic but designed for git-managed projects

## Scope

**In scope:**

- Source extraction into structured, placeholdered sentence-level records
- Profile creation with isolated translation stores, context, and identity
- Version-tracked translation tasks with immutable context snapshots
- Translation submission ingestion with linguistic auditing
- Multi-pass quality review with derivation-chain provenance
- Judge workflows for comparing/selecting/revisioning across profiles
- EPUB and Markdown output building with placeholder restoration
- Glossary management (human-curated terminology) and termbase (advanced preference storage)
- Series management for multi-book projects

**Out of scope:**

- Machine translation (booktx orchestrates agents, does not translate)
- Editing source documents
- Real-time collaboration
- Network services or APIs
- Non-Markdown/non-EPUB source formats

## Business Context

- **Source Document Input** -> Author / Publisher
  - Describe this context interface.

## Technical Context

- **Coding Agent Integration** -> LLM / Human Translator
  - Describe this context interface.
- **Version Control System** -> git
  - Describe this context interface.

# Solution Strategy

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

## Strategy Items

## Source-First, Profile-Isolated Architecture

**Drivers:**
**Constraints:**
**Related ADRs:**

The project root holds a single source document and a shared .booktx/ directory containing extracted source state (chunks, manifest, chapter map, protected names). Every translation target lives in its own translations/<profile>/ directory with its own store, context, version ledger, tasks, and output. This enforces hard isolation: no profile ever reads another profile's mutable state.

## Content-Addressed Immutability

**Drivers:**
**Constraints:**
**Related ADRs:**

Every meaningful artifact is hashed with SHA-256: source files, context snapshots, profile configs, glossary bindings, task context views. These hashes are stored in task records and version candidates so provenance is always verifiable. Tasks carry the exact context hash at creation time; task context views are immutable snapshots under context-history/views/<sha>/.

## Task Snapshot Pattern

**Drivers:**
**Constraints:**
**Related ADRs:**

When an agent requests a translation task, booktx snapshots all relevant state (context view, glossary bindings, termbase entries, config hashes) into an immutable task record. The agent works against this frozen snapshot, not mutable files. Submissions are validated against the snapshot and only then applied to the canonical store.

## Versioning Model (major.minor + R-pass.run)

**Drivers:**
**Constraints:**
**Related ADRs:**

Translation versions use major.minor refs (e.g., 1.1, 2.3). Major versions represent identity/harness/model changes tracked in the TranslationVersionLedger. Minor (subversion) bumps occur when context or baseline policy changes. Review candidates use R<pass>.<run> refs and form a derivation DAG rooted at a translation version.

## Technology Stack

**Drivers:**
**Constraints:**
**Related ADRs:**

Typer + Rich for mature CLI with structured console output. Pydantic v2 for strict JSON boundary validation. markdown-it-py for CommonMark-compliant parsing. BeautifulSoup 4 for EPUB XHTML. Hatchling + hatch-vcs for PEP 621 builds. Synchronous I/O throughout (no async needed for filesystem operations).

# Building Block View

## Top-Level Decomposition

```text
booktx/
├── bootstrap.py          # Lazy entry point, startup error rendering
├── cli.py                # Typer app assembly, command mounting
├── command_catalog.py    # Command metadata (summaries, panels, deprecations)
├── cli_support.py        # Console, project status snapshot helpers
├── config.py             # Project/path resolution, profile lifecycle
├── models.py             # Pydantic data models (68+ models)
├── translation_store.py  # V2 store read/write/migration helpers
├── chunking.py           # Source document to sentence-level records
├── chapters.py           # Chapter segmentation and mapping
├── context.py            # Context composition, views, sync
├── validate.py           # Validation engine
├── build.py              # Output artifact generation (MD/EPUB)
├── errors.py             # Structured error types
│
├── commands/             # CLI command groups (one module per Typer app)
│   ├── root.py, translate.py, review.py, judge.py, source.py,
│   ├── context.py, profile.py, epub.py, glossary.py, termbase.py,
│   ├── series.py, identity.py, guide.py, agents.py, version.py
│
├── workflows/            # Command business logic (one per command group)
│   └── (mirrors commands/ structure)
│
├── store/                # V3 shard store + storage abstraction
│   ├── detect.py, v3.py, v1_v2.py, migration.py
│   ├── models.py, paths.py, transactions.py, doctor.py
│
├── data/                 # Static data (common lemmas)
└── templates/            # Sample templates
```

## Key Modules

### `booktx.config` — Project Resolution

The `Project` dataclass is the central context object. It resolves paths for both legacy (single-`.booktx/config.toml`) and profile (`.booktx/source-config.toml` + `translations/<profile>/config.toml`) layouts. `load_project()` detects the layout, loads configuration, and optionally attaches a profile. All path-construction functions (store, tasks, ingest, output, etc.) derive from a `Project` instance.

### `booktx.models` — Data Contract

68+ Pydantic models defining every JSON artifact. Key models:

- `Chunk` / `Record` / `TranslatedChunk` — extraction and translation wire format
- `TranslationStoreV2` / `StoredTranslationRecordV2` — canonical store with nested version/review candidates
- `TranslationCandidate` / `TranslationReviewCandidate` — version and review provenance records
- `TranslationTask` / `TranslationReviewTask` — immutable task records
- `TranslationTodo` / `ReviewTodo` — bounded multi-chapter/-pass run control
- `SourceConfig` / `ProfileConfig` — TOML configuration models
- `TranslationVersionLedger` — version identity tracking

### `booktx.translation_store` — Store Logic

Operates on `TranslationStoreV2` records. Key operations:

- `ensure_store_record()` — idempotent record access/creation
- `upsert_translation_version()` — insert or update a version candidate with activation
- `effective_candidate_selection()` — resolve the output candidate (review-first, then translation)
- `review_chain_is_stale()` / `review_chain_refs()` — review provenance validation
- `legacy_store_to_v2()` / `migrate_legacy_store()` — V1 to V2 migration

### `booktx.store/` — V3 Shard Store (Opt-in)

Provides `TranslationStoreV3` with shard-per-record storage, transactions, migration from V2, and parity tests. The `manifest.json` tracks store-level metadata; each record is a directory with `current.json`, `candidates/`, and `reviews/` shards.

### `booktx.commands/` + `booktx.workflows/`

Command modules define Typer apps and CLI parameters. Workflow modules contain the business logic. This separation keeps CLI surface thin and testable independently of presentation.

## Black-Box Components

| Component                                                 | Responsibility                                                                          |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Extractor** (`chunking.py`)                             | Parse source documents into sentence-level `Record` objects with placeholder protection |
| **Chapter Mapper** (`chapters.py`)                        | Map source headings to chapters, produce `chapter-map.json`                             |
| **Translation Store** (`translation_store.py` + `store/`) | Persist and query versioned translation/review candidates                               |
| **Context Engine** (`context.py`)                         | Compose chapter notes, build context views, snapshot for tasks                          |
| **Task Factory** (`workflows/translate.py`)               | Build immutable `TranslationTask` with frozen context                                   |
| **Submission Ingest** (`workflows/translate.py`)          | Validate and ingest translated records into store                                       |
| **Review Engine** (`workflows/review.py`)                 | Select review candidates, build review tasks, ingest review submissions                 |
| **Judge Engine** (`workflows/judge.py`)                   | Compare/select/revision across profiles, produce decisions                              |
| **Builder** (`build.py`)                                  | Resolve effective candidates, restore placeholders, write output                        |
| **Validator** (`validate.py`)                             | Detect stale records, missing translations, chain drift                                 |
| **Glossary** (`glossary_*.py`)                            | Human-curated terminology with binding enforcement                                      |
| **Termbase** (`termbase*.py`)                             | Advanced reusable preference storage surface                                            |

### Level 1

#### Extractor (chunking.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Parses source documents (Markdown via markdown-it-py or EPUB via epub2text) into sentence-level Record objects. Protected names and markup spans are replaced with deterministic placeholders (**NAME_NNN**, **TAG_NNN**). Outputs chunks/NNNN.json files with Chunk schema.

#### Chapter Mapper (chapters.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Maps source document headings to chapters, producing chapter-map.json. Each chapter entry records heading text, level, and chunk coverage. Used by task creation for chapter-scoped translation and by status reporting.

#### Translation Store (translation_store.py + store/)

**Parent:** None
**Interfaces:** block-0051, block-0052
**Location:**

Persists and queries versioned translation and review candidates. V2 is a single `translation-store.json` compatibility backend with nested candidates per record. V3 is the default for new profiles and uses a manifest plus three per-chunk files (`current/<chunk>.json`, `translation-candidates/<chunk>.json`, and `review-candidates/<chunk>.json`). Each changed chunk advances one shared revision across its three shard envelopes; readers retry around publication and validate cross-shard invariants. Existing profiles remain on their detected backend.

#### Context Engine (context.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Composes chapter notes into context views. Builds effective context for tasks by selecting relevant chapter notes, glossary entries, and prior translations. Snapshots context views under context-history/views/<sha>/ for task immutability. Supports context sync between sibling profiles.

#### Task Factory (workflows/translate.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Builds immutable TranslationTask records when agents request work. Selects untranslated records, composes context windows (before/after neighbors), snapshots glossary and termbase bindings, computes all provenance hashes, and writes the task JSON with frozen context paths.

#### Submission Ingest (workflows/translate.py)

**Parent:** None
**Interfaces:** block-0051, block-0052
**Location:**

Validates translated records against their task snapshot: checks record IDs match, verifies placeholder preservation, runs optional linguistic audits (length ratios, target-language rules). On acceptance, upserts TranslationCandidate versions into the store and updates the version ledger.

#### Review Engine (workflows/review.py)

**Parent:** None
**Interfaces:** block-0051, block-0052
**Location:**

Selects records needing review per ReviewPassConfig, builds review context windows, snapshots context, creates immutable TranslationReviewTask records. Ingests review submissions by validating review chain integrity (DAG acyclicity, pass order, base hash matching) before upserting TranslationReviewCandidate.

#### Judge Engine (workflows/judge.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Cross-profile comparison, selection, and revision workflow. Loads source and selection profile stores, builds JudgeTask with candidate evidence from each source. Ingests judge decisions, records selection outcomes in selection-ledger.json.

#### Builder (build.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Resolves effective output candidates (review-first, translation-fallback), restores protected placeholders to original text, assembles target document in the requested format (Markdown or EPUB XHTML), and writes to translations/<profile>/output/.

#### Validator (validate.py)

**Parent:** None
**Interfaces:** block-0051
**Location:**

Detects stale records (source hash mismatch), missing translations, incomplete chapters, and review chain drift. Used by status and as a pre-flight check before builds.

#### Glossary Manager (glossary\_\*.py)

**Parent:** None
**Interfaces:**
**Location:**

Human-curated terminology manager. Supports binding and advisory glossary entries with source/target variants, enforcement levels (off/warn/error), usage notes, and concept kind classification. Entries can require specific target terms or forbid certain translations.

#### Termbase Manager (termbase\*.py)

**Parent:** None
**Interfaces:**
**Location:**

Advanced reusable translation preference storage. Supports phrase preferences, contextual terms, collocation preferences, word senses, style preferences, forbidden literalisms, and world-specific terms. Entries carry usage rules with regex-based source/target matching and severity levels.

#### CLI Assembly (cli.py + commands/)

**Parent:** None
**Interfaces:**
**Location:**

Assembles the Typer CLI app tree. Mounts 16 command groups (translate, review, judge, source, context, profile, epub, glossary, termbase, series, identity, guide, agents, version, doctor, root). Applies command catalog metadata for summaries and panel grouping.

#### Bootstrap (bootstrap.py)

**Parent:** None
**Interfaces:**
**Location:**

Lazy console entry point referenced by pyproject.toml [project.scripts]. Wraps booktx.cli:main in try/except. On import failure, renders concise diagnostics with exit code 70, project-data safety message, troubleshooting commands, and BOOKTX_DEBUG=1 opt-in for full traceback.

## Interfaces

### Task JSON Contract (models.py)

**Providers:**
**Consumers:**
**Protocol:**
Describe the syntax, semantics, and failure cases of this interface.

### Submission JSON Contract

**Providers:**
**Consumers:**
**Protocol:**
Describe the syntax, semantics, and failure cases of this interface.

# Runtime View

## Primary Runtime Scenarios

### RS-1: Translation Lifecycle

1. Agent runs `booktx translate next --profile de_default` to get next pending task
2. booktx resolves profile, loads store + context, selects untranslated records
3. booktx snapshots context view, glossary bindings, termbase entries, config hashes
4. booktx writes immutable `TranslationTask` JSON with frozen context paths
5. Agent translates records and runs `booktx translate submit <task-id>` with translated JSON
6. booktx validates submission against task snapshot, runs linguistic audit if configured
7. On acceptance, booktx upserts `TranslationCandidate` versions into `TranslationStoreV2`

### RS-2: Review Lifecycle

1. Agent runs `booktx review next --profile de_default --pass 1`
2. booktx resolves `ReviewPassConfig`, selects records with missing/stale review for pass
3. booktx computes review context window (N before + N after records)
4. booktx snapshots review context, writes immutable `TranslationReviewTask`
5. Agent reviews and runs `booktx review submit <task-id>` with review candidates
6. booktx validates review chain (DAG acyclicity, pass order, base integrity)
7. On acceptance, booktx upserts `TranslationReviewCandidate` into store

### RS-3: Build Output

1. User runs `booktx build --profile de_default`
2. For each record, booktx resolves effective candidate:
   - Prefer active `TranslationReviewCandidate` if chain-valid
   - Fall back to active `TranslationCandidate`
3. booktx restores protected placeholders (`__NAME_NNN__`, `__TAG_NNN__`)
4. booktx assembles target document (Markdown or EPUB XHTML)
5. Output written to `translations/<profile>/output/`

### RS-4: Judge Workflow

1. User runs `booktx judge task-next --profile judge_profile --sources sourceA sourceB`
2. booktx loads source store and selection profiles, builds `JudgeTask`
3. Agent evaluates candidates and runs `booktx judge submit <task-id>` with decisions
4. booktx records decisions in `selection-ledger.json`

## Error Handling Runtime

On CLI startup, `booktx.bootstrap.main()` wraps `booktx.cli.main()` in a try/except. Import errors render a concise message with exit code 70 and `BOOKTX_DEBUG=1` opt-in for full tracebacks. Malformed command catalog metadata emits one warning and falls back to native Typer help; strict validation is reserved for explicit test/release checks.

## Translation Lifecycle

1. Agent runs 'booktx translate next --profile de_default'. 2. booktx resolves profile, loads store + context, selects untranslated records. 3. booktx snapshots context view, glossary bindings, termbase entries, config hashes. 4. booktx writes immutable TranslationTask JSON with frozen context paths. 5. Agent translates records. 6. Agent runs 'booktx translate submit <task-id>' with translated JSON. 7. booktx validates submission against task snapshot, runs linguistic audit if configured. 8. On acceptance, booktx upserts TranslationCandidate versions into TranslationStoreV2.

## Review Lifecycle

1. Agent runs 'booktx review next --profile de_default --pass 1'. 2. booktx resolves ReviewPassConfig, selects records with missing/stale review for pass. 3. booktx computes review context window (N before + N after records). 4. booktx snapshots review context, writes immutable TranslationReviewTask. 5. Agent reviews and runs 'booktx review submit <task-id>' with review candidates. 6. booktx validates review chain (DAG acyclicity, pass order, base integrity). 7. On acceptance, booktx upserts TranslationReviewCandidate into store.

## Build Output

1. User runs 'booktx build --profile de_default'. 2. For each record, booktx resolves effective candidate: prefer active TranslationReviewCandidate if chain-valid, else fall back to active TranslationCandidate. 3. booktx restores protected placeholders. 4. booktx assembles target document (Markdown or EPUB XHTML). 5. Output written to translations/<profile>/output/.

## Judge Workflow

1. User runs 'booktx judge task-next --profile judge_profile --sources sourceA sourceB'. 2. booktx loads source store and selection profiles, builds JudgeTask with candidate evidence. 3. Agent evaluates candidates. 4. Agent runs 'booktx judge submit <task-id>' with decisions. 5. booktx records decisions in selection-ledger.json.

## CLI Startup Error Handling

On CLI invocation: 1. Python loads booktx.bootstrap:main. 2. bootstrap.main() tries to import booktx.cli.main. 3. On import error: renders concise message with exception class/message, exit code 70, project-data safety statement, troubleshooting commands, and BOOKTX_DEBUG hint. 4. On success: delegates to cli.main() for normal operation. 5. Malformed command catalog metadata emits one warning and falls back to native Typer help.

# Deployment View

## Deployment Context

booktx is a Python CLI tool distributed as a pip-installable wheel. It has no server, no daemon, and no network service. Deployment means installing the package into a Python environment.

### Installation

```bash
pip install booktx
```

Or from source:

```bash
pip install .
# or: pip install -e .[dev,docs]
```

### Runtime Dependencies

booktx requires Python >= 3.10 and the following core dependencies:

- `typer` — CLI framework
- `rich` — Console output formatting
- `pydantic>=2` — Data validation
- `tomli-w` / `tomli` — TOML write/read
- `beautifulsoup4` — EPUB XHTML parsing
- `markdown-it-py` — Markdown parsing
- `phrasplit>=0.3.3` — Sentence splitting
- `epub2text>=0.2.7` — EPUB extraction
- `text2epub>=0.1.4` — EPUB generation

Optional: `spacy>=3.7,<4` for source analysis features.

### Filesystem Layout

```text
~/.config/booktx/translation-termbase/  # User-global termbase shards
                                           # (override with BOOKTX_TERMBASE_DIR)
```

### Environments

| Environment  | Python Versions | Purpose                                               |
| ------------ | --------------- | ----------------------------------------------------- |
| Development  | 3.10+           | Local editing, tests, linting                         |
| CI (PR)      | 3.10, 3.13      | Quality gate, import-health checks, wheel smoke tests |
| CI (Release) | 3.13            | Build + publish to PyPI                               |
| Production   | 3.10+           | End-user pip install                                  |

### CI/CD

- **Quality gate** (`scripts/quality_gate.py`): compile checks, focused tests, full pytest, Ruff, mypy, wheel build, clean-environment install, CLI help smoke tests. Stops at first failure.
- **GitHub Actions**: PR and release-branch workflows enforce the gate on Python 3.10 and 3.13.
- **Publish**: `python-publish.yml` requires the quality gate for the exact checked-out commit before publishing to PyPI.

## Development Environment

Local development uses pip install -e .[dev,docs] with editable install. Dependencies managed via pyproject.toml. Testing via pytest, linting via Ruff, type checking via mypy --strict. Quality gate script runs the full validation pipeline locally.

## CI Environment (GitHub Actions)

GitHub Actions workflows: quality.yml enforces the quality gate on PRs and release branches for Python 3.10 and 3.13. python-publish.yml requires the quality gate to pass for the exact checked-out commit before publishing to PyPI. Build artifacts include the wheel and sdist.

## PyPI Release

Release workflow: git tag triggers python-publish.yml. hatchling builds the wheel and sdist. The quality gate must pass for the tagged commit. Artifacts published to PyPI with exact commit evidence. Version derived from VCS via hatch-vcs.

## End-User Installation

End users install via pip install booktx. Requires Python >= 3.10. User-global termbase stored in ~/.config/booktx/translation-termbase/ (override via BOOKTX_TERMBASE_DIR). No configuration needed beyond pip install.

# Cross-cutting Concepts

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

## Placeholder Protection

During extraction, booktx replaces non-translatable spans (proper names, HTML/XML tags) with deterministic tokens (**NAME_001**, **TAG_001**). The Placeholder model records the token-to-original mapping. The translation agent sees only placeholdered text; the build step restores originals verbatim. This guarantees that markup and protected names survive round-trip without agent awareness.

## Content-Addressed Hashing

Every config, context view, glossary binding, and task snapshot is SHA-256 hashed. These hashes travel with task records, version candidates, and review candidates. Validation compares stored hashes against current filesystem state to detect drift. Enables staleness detection, context drift detection, and review chain staleness verification.

## Version Provenance

The TranslationVersionLedger records who produced each major version (actor, harness, model) and each subversion's context hash. TranslationCandidate and TranslationReviewCandidate models carry baseline refs, context view hashes, and config hashes. This creates a complete provenance trail from source to output.

## Strict vs. Lazy Validation

booktx distinguishes two validation modes: Runtime/startup where malformed optional metadata emits one warning and falls back (CLI must never refuse to start due to cosmetic metadata issues); and Strict mode for tests/release where command_catalog.py strict validation and scripts/quality_gate.py catch all issues before release.

## Profile Isolation

Profiles are hard-isolated leaf directories. A build or validation run resolves exactly one profile. The only cross-profile operations are explicit comparison commands (profile compare, judge). No profile reads another profile's store during normal operations.

## Agent Protocol

The agent interaction follows a strict contract: 1) Agent requests next task, receives immutable JSON with frozen context. 2) Agent processes task against the snapshot (never mutates files directly). 3) Agent submits results, booktx validates and applies to canonical store. 4) AgentNextAction model signals continue, complete, or blocked with safe-next-command hint.

## Todo Lifecycle

TranslationTodo and ReviewTodo are durable run-control artifacts for bounded multi-chapter/-pass agent runs. They carry scope, stop conditions, starting totals, and chapter lists. A companion TranslationTodoLifecycle tracks mutable state (open/completed/abandoned/superseded) separately from the immutable todo.

## Linguistic Auditing

Optional submission-time checks: placeholder integrity, suspicious length ratios, and target-language rule validation. Configured via SubmissionQualityConfig in profile config. Findings are non-blocking warnings by default.

# Architecture Decisions

## Architecture Decision Records

### ADR-1: Single-File V2 Store as Default

**Status:** Accepted

**Context:** The original V1 flat store (`TranslationStore`) was a simple dict of record IDs to targets. It lacked source text anchoring, version tracking, and review provenance.

**Decision:** `TranslationStoreV2` nests `TranslationCandidate` versions and `TranslationReviewCandidate` reviews inside each `StoredTranslationRecordV2`. The store is a single JSON file per profile.

**Consequences:**

- (+) Simple to inspect, backup, and version-control
- (+) Full provenance per record: source SHA, active version, active review, candidate history
- (-) Large stores still require materialization at some compatibility boundaries; v3 bounded edits and readiness benchmarks track this tradeoff

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

- (+) Agents get deterministic resolution at profile boundary
- (+) Marker validation prevents stale/inconsistent markers
- (-) Extra file per profile (acceptable trade-off)

## Single-File V2 Store as Default

**Document version:** 2

## Context

The original V1 flat store (TranslationStore) was a simple dict of record IDs to targets. It lacked source text anchoring, version tracking, and review provenance.

## Decision

TranslationStoreV2 nests TranslationCandidate versions and TranslationReviewCandidate reviews inside each StoredTranslationRecordV2. The store is a single JSON file per profile.

## Consequences

- (+) Simple to inspect, backup, and version-control
- (+) Full provenance per record: source SHA, active version, active review, candidate history
- (-) Large stores still require materialization at some compatibility boundaries; v3 bounded edits and readiness benchmarks track this tradeoff

## Typer with Command Catalog Fallback

**Document version:** 2

## Context

Typer provides native help and command registration. But command metadata (summaries, panel groupings, deprecated commands) must not block CLI startup if malformed.

## Decision

command_catalog.py defines SUMMARY_OVERRIDES and panel metadata as typed dicts. apply_command_catalog() applies them at startup. If values are invalid, a warning is emitted and native Typer help is used. Strict validation exists separately for tests and release tooling.

## Consequences

- (+) CLI never fails to start due to cosmetic metadata issues
- (+) Strict checks catch regressions before release
- (-) Dual validation paths must stay synchronized

## Lazy Bootstrap Entry Point

**Document version:** 2

## Context

A typo in command_catalog.py (SUMMARY_OVERRIDES value as tuple instead of string) caused TypeError during CLI import, producing an unfriendly traceback.

## Decision

pyproject.toml console script points to booktx.bootstrap:main, which wraps booktx.cli:main in a try/except. Import failures render concise diagnostics with exit code 70, project-data safety message, and BOOKTX_DEBUG=1 opt-in.

## Consequences

- (+) Users get actionable error messages instead of raw tracebacks
- (+) Project data is never modified on startup failure
- (-) Adds one extra module in the import chain

## Review Pass Order as Lexicographic DAG

**Document version:** 2

## Context

Multi-pass reviews need a clear ordering constraint. A review based on another review should only move forward.

## Decision

Review refs use R<pass>.<run> format. The total order is lexicographic on (pass_number, run_number). A review based on another review must satisfy (new.pass, new.run) > (base.pass, base.run). This permits same-pass reruns (R1.2 from R1.1) and higher-pass reviews from lower-pass (R2.1 from R1.2), while rejecting cycles and regression.

## Consequences

- (+) Clear, enforceable ordering
- (+) Cycle detection via graph walk in \_validate_review_graph_is_acyclic()
- (-) Review consumers must understand the lexicographic constraint

## Profile Root Markers for Agent Isolation

**Document version:** 2

## Context

Coding agents need unambiguous project/profile resolution without guessing. Running booktx from a profile root should work deterministically.

## Decision

write_profile_root_marker() writes .booktx-profile.json containing profile name, source identity, and target locale. A command run from a profile root resolves the profile from this marker and treats '.' as the project root. No implicit single-profile resolution exists.

## Consequences

- (+) Agents get deterministic resolution at profile boundary
- (+) Marker validation prevents stale/inconsistent markers
- (-) Extra file per profile (acceptable trade-off)

# Quality Requirements

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

## Quality Requirements Overview

| Title                       | Category        | Measure | Scenarios |
| --------------------------- | --------------- | ------- | --------- |
| Deterministic Builds        | reliability     |         |           |
| Profile Isolation Guarantee | security        |         |           |
| Agent Submission Safety     | security        |         |           |
| Startup Robustness          | reliability     |         |           |
| Test Coverage               | maintainability |         |           |
| Type Safety (mypy strict)   | maintainability |         |           |
| Lint Compliance (Ruff)      | maintainability |         |           |

## Quality Scenarios

<!-- archledger: no accepted records for this section yet -->

# Risks and Technical Debt

## Risks

### RISK-1: V2 Store Scalability

**Severity:** Medium | **Probability:** Medium

Large books (100k+ records) produce multi-megabyte `translation-store.json` files. The single-file V2 store loads entirely into memory.

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

## Risk Overview

| Title                         | Severity | Probability | Mitigation                                                                                                                                                                                                                    | Notes                                                                                                                                                                                                               |
| ----------------------------- | -------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| V2 Store Scalability          | medium   | medium      | V3 is the new-profile default, uses bounded per-chunk writes, shared reader revisions, recovery journals, doctor inventory, and explicit v2↔v3 migration/rollback. Parity and readiness-gate tests validate the two backends. | Large books (100k+ records) produce multi-megabyte translation-store.json files. The single-file V2 store loads entirely into memory. Severity: Medium                                                              | Probability: Medium Mitigation: V3 is the new-profile default, uses bounded per-chunk writes, shared reader revisions, recovery journals, doctor inventory, and explicit v2↔v3 migration/rollback. Parity and readiness-gate tests validate the two backends. |
| Agent Context Window Overflow | medium   | medium      | batch_words and before_records/after_records config options; include_untranslated_neighbors toggle; todo max_run_words cap                                                                                                    | Large chapters with many context records may exceed LLM context windows. Task word budgets and context window sizes are configurable but not automatically enforced against external model limits. Severity: Medium | Probability: Medium Mitigation: batch_words and before_records/after_records config options. include_untranslated_neighbors toggle for review tasks. Todo max_run_words cap.                                                                                  |
| Placeholder Collision         | low      | low         | Placeholder tokens use distinct pattern unlikely in natural text; custom patterns configurable via SourceAnalysisPatternsConfig                                                                                               | If source text contains literal **NAME_NNN**-like strings, extraction may produce false placeholder matches. Severity: Low                                                                                          | Probability: Low Mitigation: Placeholder tokens use a distinct pattern unlikely in natural text. Custom patterns configurable via SourceAnalysisPatternsConfig.                                                                                               |
| Source Format Drift           | medium   | low         | Pinned dependency versions in pyproject.toml; manifest records source SHA-256; extraction checks for source drift; test suite covers format-specific edge cases                                                               | EPUB or Markdown parsing libraries may change behavior across versions, affecting extraction output. Severity: Medium                                                                                               | Probability: Low Mitigation: Pinned dependency versions in pyproject.toml. Manifest records source SHA-256; extraction checks for source drift. Test suite covers format-specific edge cases.                                                                 |

# Glossary

## Glossary

| Term                      | Definition                                                                                         |
| ------------------------- | -------------------------------------------------------------------------------------------------- |
| **Chunk**                 | A JSON file (`chunks/NNNN.json`) containing up to `chunk_size` source records                      |
| **Record**                | A single translatable unit (sentence or paragraph) with placeholdered text                         |
| **Placeholder**           | A non-translatable span replaced by a token (`__NAME_001__`, `__TAG_001__`) during extraction      |
| **Profile**               | An isolated translation target under `translations/<profile>/` with own store, context, and output |
| **Translation Store**     | The canonical record-level translation state (`translation-store.json`)                            |
| **Translation Candidate** | One versioned translation of a record (`version_ref` like `1.1`)                                   |
| **Review Candidate**      | One quality-improved review output (`review_ref` like `R1.2`)                                      |
| **Version Ledger**        | `translation-version-ledger.json` tracking identity per major version                              |
| **Translation Task**      | An immutable work item with frozen context snapshot                                                |
| **Translation Todo**      | A durable run-control artifact for bounded multi-chapter agent translation runs                    |
| **Review Todo**           | A durable run-control artifact for bounded multi-pass review runs                                  |
| **Context View**          | A frozen snapshot of effective context used by a task, stored under `context-history/views/<sha>/` |
| **Glossary**              | Human-curated terminology decisions (binding or advisory) managed by `booktx glossary`             |
| **Termbase**              | Advanced reusable preference storage for translation patterns, managed by `booktx termbase`        |
| **Judge**                 | Cross-profile comparison/selection/revision workflow                                               |
| **Series**                | Multi-book project coordination under `booktx series`                                              |
| **Source Config**         | `.booktx/source-config.toml` defining source language, file, format, and chunk size                |
| **Profile Config**        | `translations/<profile>/config.toml` defining target language, locale, and output                  |
| **Profile Root Marker**   | `.booktx-profile.json` enabling agent-friendly profile resolution from a profile root              |
| **Effective Candidate**   | The output target for a record: active review if chain-valid, else active translation              |
| **Submission Ingest**     | Validating and applying translated/reviewed records into the canonical store                       |
| **Canonical Record ID**   | Formatted as `chunk_id-part_id` (e.g., `0001-000042`)                                              |
| **Quality Gate**          | `scripts/quality_gate.py` — ordered checks stopping at first failure                               |
| **Bootstrap**             | `booktx.bootstrap.main()` — lazy entry point with startup error containment                        |
| **Names File**            | `.booktx/names.json` — manually curated protected terms                                            |
| **Source Manifest**       | `.booktx/source-manifest.json` — extraction metadata and source SHA-256                            |
| **Chapter Map**           | `.booktx/chapter-map.json` — chapter-to-chunk mapping                                              |

| Term                  | Definition                                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Chunk                 | A JSON file (chunks/NNNN.json) containing up to chunk_size source records extracted from the source document.                                                      |
| Record                | A single translatable unit (sentence or paragraph) with placeholdered text, identified by a canonical record ID like 0001-000042.                                  |
| Placeholder           | A non-translatable span replaced by a deterministic token (**NAME_001**, **TAG_001**) during extraction. The original text is restored verbatim during build.      |
| Profile               | An isolated translation target under translations/<profile>/ with its own store, context, version ledger, tasks, and output.                                       |
| Translation Store     | The canonical record-level translation state file (translation-store.json) containing StoredTranslationRecordV2 entries with nested version and review candidates. |
| Translation Candidate | One versioned translation of a record, identified by a version_ref like 1.1, carrying target text, baseline refs, context hashes, and provenance metadata.         |
| Review Candidate      | One quality-improved review output identified by a review_ref like R1.2, derived from a translation or earlier review base with provenance chain validation.       |
| Version Ledger        | translation-version-ledger.json tracking identity (actor, harness, model) per major version and context hash per subversion.                                       |
| Translation Task      | An immutable work item returned by translate next, containing records to translate with frozen context view paths, glossary/termbase bindings, and config hashes.  |
| Translation Todo      | A durable run-control artifact for bounded multi-chapter agent translation runs, carrying scope, stop conditions, and chapter lists.                               |
| Review Todo           | A durable run-control artifact for bounded multi-pass review runs, carrying pass selection modes, chapter lists, and stop conditions.                              |
| Context View          | A frozen snapshot of effective context used by a task, stored under context-history/views/<sha>/. Immutable once created.                                          |
| Glossary (booktx)     | Human-curated terminology decisions (binding or advisory) with source/target variants, enforcement levels, and usage notes. Managed by booktx glossary.            |
| Termbase              | Advanced reusable preference storage for translation patterns (phrase preferences, collocations, word senses, forbidden literalisms). Managed by booktx termbase.  |
| Judge                 | Cross-profile comparison, selection, and revision workflow for evaluating translation quality across profiles.                                                     |
| Series                | Multi-book project coordination under booktx series, enabling shared termbase and cross-book context management.                                                   |
| Source Config         | .booktx/source-config.toml defining source language, source file, format (markdown/epub), and chunk size.                                                          |
| Profile Config        | translations/<profile>/config.toml defining target language, locale, output filename, identity defaults, and review configuration.                                 |
| Profile Root Marker   | .booktx-profile.json enabling agent-friendly profile resolution from a profile root directory, containing profile name, source identity, and target locale.        |
| Effective Candidate   | The output target for a record: the active TranslationReviewCandidate if chain-valid, otherwise the active TranslationCandidate.                                   |
| Submission Ingest     | The process of validating and applying translated or reviewed records into the canonical translation store.                                                        |
| Canonical Record ID   | Formatted as chunk_id-part_id (e.g., 0001-000042), the unique identifier for a source record within a project.                                                     |
| Quality Gate          | scripts/quality_gate.py — ordered checks (compile, focused tests, full pytest, Ruff, mypy, build, wheel install, CLI smoke tests) stopping at first failure.       |
| Bootstrap             | booktx.bootstrap.main() — the lazy console entry point that wraps CLI import in try/except for startup error containment.                                          |
| Names File            | .booktx/names.json — manually curated list of protected terms that are placeholdered during extraction and restored during build.                                  |
| Source Manifest       | .booktx/source-manifest.json — extraction metadata including source file SHA-256, chunk listing, and format information.                                           |
| Chapter Map           | .booktx/chapter-map.json — mapping of source document headings to chapters with chunk coverage information.                                                        |

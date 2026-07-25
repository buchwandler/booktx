---
schema_version: 4
id: content-0005
kind: content
type: section
section: building_block_view
title: Building Block View
order: 50
status: accepted
version: 2
body_format: markdown
---

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

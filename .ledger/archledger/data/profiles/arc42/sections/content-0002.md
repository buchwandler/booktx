---
schema_version: 4
id: content-0002
kind: content
type: section
section: architecture_constraints
title: Architecture Constraints
order: 20
status: accepted
version: 2
body_format: markdown
---

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

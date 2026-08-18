---
schema_version: 4
id: content-0012
kind: content
type: section
section: glossary
title: Glossary
order: 120
status: accepted
version: 3
body_format: markdown
---

## Glossary

| Term                      | Definition                                                                                                                                                                                                           |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Chunk**                 | A JSON file (`chunks/NNNN.json`) containing up to `chunk_size` source records                                                                                                                                        |
| **Record**                | A single translatable unit (sentence or paragraph) with placeholdered text                                                                                                                                           |
| **Placeholder**           | A non-translatable span replaced by a token (`__NAME_001__`, `__TAG_001__`) during extraction                                                                                                                        |
| **Profile**               | An isolated translation target under `translations/<profile>/` with own store, context, and output                                                                                                                   |
| **Translation Store**     | The logical canonical record-level translation repository for one profile; v3 `translation-store/` is the default backend for new profiles and v2 `translation-store.json` remains a supported compatibility backend |
| **Translation Candidate** | One versioned translation of a record (`version_ref` like `1.1`)                                                                                                                                                     |
| **Review Candidate**      | One quality-improved review output (`review_ref` like `R1.2`)                                                                                                                                                        |
| **Version Ledger**        | `translation-version-ledger.json` tracking identity per major version                                                                                                                                                |
| **Translation Task**      | An immutable work item with frozen context snapshot                                                                                                                                                                  |
| **Translation Todo**      | A durable run-control artifact for bounded multi-chapter agent translation runs                                                                                                                                      |
| **Review Todo**           | A durable run-control artifact for bounded multi-pass review runs                                                                                                                                                    |
| **Context View**          | A frozen snapshot of effective context used by a task, stored under `context-history/views/<sha>/`                                                                                                                   |
| **Glossary**              | Human-curated terminology decisions (binding or advisory) managed by `booktx glossary`                                                                                                                               |
| **Termbase**              | Advanced reusable preference storage for translation patterns, managed by `booktx termbase`                                                                                                                          |
| **Judge**                 | Cross-profile comparison/selection/revision workflow                                                                                                                                                                 |
| **Series**                | Multi-book project coordination under `booktx series`                                                                                                                                                                |
| **Source Config**         | `.booktx/source-config.toml` defining source language, file, format, and chunk size                                                                                                                                  |
| **Profile Config**        | `translations/<profile>/config.toml` defining target language, locale, and output                                                                                                                                    |
| **Profile Root Marker**   | `.booktx-profile.json` enabling agent-friendly profile resolution from a profile root                                                                                                                                |
| **Effective Candidate**   | The output target for a record: active review if chain-valid, else active translation                                                                                                                                |
| **Submission Ingest**     | Validating and applying translated/reviewed records into the canonical store                                                                                                                                         |
| **Canonical Record ID**   | Formatted as `chunk_id-part_id` (e.g., `0001-000042`)                                                                                                                                                                |
| **Quality Gate**          | `scripts/quality_gate.py` — ordered checks stopping at first failure                                                                                                                                                 |
| **Bootstrap**             | `booktx.bootstrap.main()` — lazy entry point with startup error containment                                                                                                                                          |
| **Names File**            | `.booktx/names.json` — manually curated protected terms                                                                                                                                                              |
| **Source Manifest**       | `.booktx/source-manifest.json` — extraction metadata and source SHA-256                                                                                                                                              |
| **Chapter Map**           | `.booktx/chapter-map.json` — chapter-to-chunk mapping                                                                                                                                                                |

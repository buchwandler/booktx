---
schema_version: 4
id: content-0003
kind: content
type: section
section: context_and_scope
title: Context and Scope
order: 30
status: accepted
version: 2
body_format: markdown
---

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

---
schema_version: 4
id: content-0001
kind: content
type: section
section: introduction_and_goals
title: Introduction and Goals
order: 10
status: accepted
version: 2
body_format: markdown
---

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

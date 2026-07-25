---
schema_version: 4
id: content-0014
kind: content
type: stakeholder
title: Coding Agents (LLMs)
status: accepted
section: introduction_and_goals
order: 20
version: 3
contact: ""
expectations:
  - Deterministic JSON contracts
  - Immutable task context snapshots
  - Explicit state-transition rules
  - No direct file mutation outside task contract
body_format: markdown
---

Coding agents (LLMs) consume deterministic JSON contracts: TranslationTask, TranslationReviewTask, and JudgeTask. Every task is an immutable snapshot with frozen context paths. Agents must never inspect parent directories, edit source files, or modify project data outside the task contract. The SKILL.md and generated AGENTS.md enforce isolation rules.

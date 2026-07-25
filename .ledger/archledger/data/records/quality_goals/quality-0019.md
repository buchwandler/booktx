---
schema_version: 4
id: quality-0019
kind: quality
type: quality_goal
title: Agent-Safe Boundaries
status: accepted
section: introduction_and_goals
order: 30
version: 4
priority: 3
scenario:
  An agent submits a translation with tampered context hash; booktx rejects
  without mutating the store
body_format: markdown
---

Agents operate on immutable task snapshots, never on mutable files. Submissions are validated against the task snapshot before any store mutation occurs. The TranslationTask carries frozen context paths, config hashes, and glossary/termbase bindings. The AgentNextAction model signals continue/complete/blocked with a safe-next-command hint.

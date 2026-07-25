---
schema_version: 4
id: concept-0067
kind: concept
type: concept
title: Agent Protocol
status: accepted
section: cross_cutting_concepts
order: 60
version: 2
applies_to: []
body_format: markdown
---

The agent interaction follows a strict contract: 1) Agent requests next task, receives immutable JSON with frozen context. 2) Agent processes task against the snapshot (never mutates files directly). 3) Agent submits results, booktx validates and applies to canonical store. 4) AgentNextAction model signals continue, complete, or blocked with safe-next-command hint.

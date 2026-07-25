---
schema_version: 4
id: block-0041
kind: block
type: black_box
title: Task Factory (workflows/translate.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 50
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - task
  - snapshot
  - translation
body_format: markdown
---

Builds immutable TranslationTask records when agents request work. Selects untranslated records, composes context windows (before/after neighbors), snapshots glossary and termbase bindings, computes all provenance hashes, and writes the task JSON with frozen context paths.

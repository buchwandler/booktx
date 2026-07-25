---
schema_version: 4
id: strategy-0034
kind: strategy
type: strategy_item
title: Task Snapshot Pattern
status: accepted
section: solution_strategy
order: 30
version: 2
drivers: []
constraints: []
related_adrs: []
body_format: markdown
---

When an agent requests a translation task, booktx snapshots all relevant state (context view, glossary bindings, termbase entries, config hashes) into an immutable task record. The agent works against this frozen snapshot, not mutable files. Submissions are validated against the snapshot and only then applied to the canonical store.

---
schema_version: 4
id: strategy-0033
kind: strategy
type: strategy_item
title: Content-Addressed Immutability
status: accepted
section: solution_strategy
order: 20
version: 2
drivers: []
constraints: []
related_adrs: []
body_format: markdown
---

Every meaningful artifact is hashed with SHA-256: source files, context snapshots, profile configs, glossary bindings, task context views. These hashes are stored in task records and version candidates so provenance is always verifiable. Tasks carry the exact context hash at creation time; task context views are immutable snapshots under context-history/views/<sha>/.

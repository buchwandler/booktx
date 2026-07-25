---
schema_version: 4
id: strategy-0032
kind: strategy
type: strategy_item
title: Source-First, Profile-Isolated Architecture
status: accepted
section: solution_strategy
order: 10
version: 2
drivers: []
constraints: []
related_adrs: []
body_format: markdown
---

The project root holds a single source document and a shared .booktx/ directory containing extracted source state (chunks, manifest, chapter map, protected names). Every translation target lives in its own translations/<profile>/ directory with its own store, context, version ledger, tasks, and output. This enforces hard isolation: no profile ever reads another profile's mutable state.

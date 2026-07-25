---
schema_version: 4
id: block-0040
kind: block
type: black_box
title: Context Engine (context.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 40
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - context
  - snapshot
  - hashing
body_format: markdown
---

Composes chapter notes into context views. Builds effective context for tasks by selecting relevant chapter notes, glossary entries, and prior translations. Snapshots context views under context-history/views/<sha>/ for task immutability. Supports context sync between sibling profiles.

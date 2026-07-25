---
schema_version: 4
id: block-0039
kind: block
type: black_box
title: Translation Store (translation_store.py + store/)
status: accepted
section: building_block_view
level: 1
parent: null
order: 30
version: 4
interfaces:
  - block-0051
  - block-0052
location: []
fulfilled_requirements: []
risks: []
tags:
  - store
  - translation
  - review
  - state
body_format: markdown
---

Persists and queries versioned translation and review candidates. V2 store is a single translation-store.json with nested candidates per record. V3 store (opt-in) is shard-based under translation-store/ with per-record directories. Supports candidate upsert, activation, effective resolution, and chain validation.

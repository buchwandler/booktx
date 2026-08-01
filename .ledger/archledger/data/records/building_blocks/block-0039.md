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
version: 5
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

Persists and queries versioned translation and review candidates. V2 is a single `translation-store.json` compatibility backend with nested candidates per record. V3 is the default for new profiles and uses a manifest plus three per-chunk files (`current/<chunk>.json`, `translation-candidates/<chunk>.json`, and `review-candidates/<chunk>.json`). Each changed chunk advances one shared revision across its three shard envelopes; readers retry around publication and validate cross-shard invariants. Existing profiles remain on their detected backend.

---
schema_version: 4
id: block-0037
kind: block
type: black_box
title: Extractor (chunking.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 10
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - extraction
  - source
  - chunking
body_format: markdown
---

Parses source documents (Markdown via markdown-it-py or EPUB via epub2text) into sentence-level Record objects. Protected names and markup spans are replaced with deterministic placeholders (**NAME_NNN**, **TAG_NNN**). Outputs chunks/NNNN.json files with Chunk schema.

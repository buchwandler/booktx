---
schema_version: 4
id: block-0038
kind: block
type: black_box
title: Chapter Mapper (chapters.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 20
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - chapters
  - mapping
  - source
body_format: markdown
---

Maps source document headings to chapters, producing chapter-map.json. Each chapter entry records heading text, level, and chunk coverage. Used by task creation for chapter-scoped translation and by status reporting.

---
schema_version: 4
id: block-0045
kind: block
type: black_box
title: Builder (build.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 90
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - build
  - output
  - placeholder-restoration
body_format: markdown
---

Resolves effective output candidates (review-first, translation-fallback), restores protected placeholders to original text, assembles target document in the requested format (Markdown or EPUB XHTML), and writes to translations/<profile>/output/.

---
schema_version: 4
id: block-0044
kind: block
type: black_box
title: Judge Engine (workflows/judge.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 80
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - judge
  - comparison
  - selection
body_format: markdown
---

Cross-profile comparison, selection, and revision workflow. Loads source and selection profile stores, builds JudgeTask with candidate evidence from each source. Ingests judge decisions, records selection outcomes in selection-ledger.json.

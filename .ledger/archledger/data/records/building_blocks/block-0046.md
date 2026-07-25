---
schema_version: 4
id: block-0046
kind: block
type: black_box
title: Validator (validate.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 100
version: 4
interfaces:
  - block-0051
location: []
fulfilled_requirements: []
risks: []
tags:
  - validation
  - drift
  - status
body_format: markdown
---

Detects stale records (source hash mismatch), missing translations, incomplete chapters, and review chain drift. Used by status and as a pre-flight check before builds.

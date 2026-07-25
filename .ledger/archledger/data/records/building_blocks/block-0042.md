---
schema_version: 4
id: block-0042
kind: block
type: black_box
title: Submission Ingest (workflows/translate.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 60
version: 4
interfaces:
  - block-0051
  - block-0052
location: []
fulfilled_requirements: []
risks: []
tags:
  - submission
  - validation
  - ingest
body_format: markdown
---

Validates translated records against their task snapshot: checks record IDs match, verifies placeholder preservation, runs optional linguistic audits (length ratios, target-language rules). On acceptance, upserts TranslationCandidate versions into the store and updates the version ledger.

---
schema_version: 4
id: block-0043
kind: block
type: black_box
title: Review Engine (workflows/review.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 70
version: 4
interfaces:
  - block-0051
  - block-0052
location: []
fulfilled_requirements: []
risks: []
tags:
  - review
  - quality
  - provenance
body_format: markdown
---

Selects records needing review per ReviewPassConfig, builds review context windows, snapshots context, creates immutable TranslationReviewTask records. Ingests review submissions by validating review chain integrity (DAG acyclicity, pass order, base hash matching) before upserting TranslationReviewCandidate.

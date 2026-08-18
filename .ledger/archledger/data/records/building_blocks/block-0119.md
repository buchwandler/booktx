---
schema_version: 4
id: block-0119
kind: block
type: black_box
title: Judge Presenters (commands/judge_presenters.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 210
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - judge
  - presentation
  - cli
body_format: markdown
---

Presentation helpers for judge command output. Renders judge artifact paths relative to profile root (in profile-root mode) or project root (in project mode). Builds sync render payloads with profile snapshots, manifest display paths, and next-action hints for the judge workflow.

---
schema_version: 4
id: block-0114
kind: block
type: black_box
title: Quality Benchmark (quality_benchmark.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 160
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - quality
  - benchmark
  - ci
body_format: markdown
---

Deterministic benchmark runner for first-pass linguistic regression cases. Loads quality fixture files (JSON lists of source/bad_target pairs), runs `audit_text()` against each case, and produces a `QualityBenchmarkReport` with builtin recall, false-positive counts, and total case metrics. Used by CI and local development to detect regressions in the built-in linguistic audit rules.

---
schema_version: 4
id: block-0115
kind: block
type: black_box
title: Source Analysis Snapshot (source_analysis_snapshot.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 170
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - source-analysis
  - snapshot
body_format: markdown
---

Snapshot read/write helpers extracted from `booktx.source_analysis`. Builds profile-scoped `SourceAnalysisSnapshot` envelopes wrapping canonical `SourceAnalysisReport` data. Validates snapshot payloads by checking schema version, envelope flags, and recomputing the embedded `analysis_sha256` digest to detect tampering.

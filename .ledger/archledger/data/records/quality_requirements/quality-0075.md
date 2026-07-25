---
schema_version: 4
id: quality-0075
kind: quality
type: quality_requirement
title: Deterministic Builds
status: accepted
section: quality_requirements
order: 10
version: 3
category: reliability
source: ""
measure: ""
scenarios: []
body_format: markdown
---

Given identical source, context, and profile config, booktx build must produce byte-for-byte identical output. All hashing is SHA-256; all JSON serialization is deterministic with sort_keys=True. Measurement: run build twice, compare SHA-256 of output files.

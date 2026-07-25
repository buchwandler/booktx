---
schema_version: 4
id: quality-0077
kind: quality
type: quality_requirement
title: Agent Submission Safety
status: accepted
section: quality_requirements
order: 30
version: 3
category: security
source: ""
measure: ""
scenarios: []
body_format: markdown
---

A malformed or malicious submission must never corrupt the canonical store. Submissions are validated against task snapshots before any store mutation. Measurement: test suite covers invalid submissions (wrong IDs, missing records, tampered context hashes).

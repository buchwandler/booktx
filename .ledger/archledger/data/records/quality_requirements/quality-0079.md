---
schema_version: 4
id: quality-0079
kind: quality
type: quality_requirement
title: Test Coverage
status: accepted
section: quality_requirements
order: 50
version: 3
category: maintainability
source: ""
measure: ""
scenarios: []
body_format: markdown
---

All command workflows, store operations, validation rules, and data models must have test coverage. Quality gate enforces full test suite pass before release. Measurement: python -m pytest -q must pass; scripts/quality_gate.py must exit 0.

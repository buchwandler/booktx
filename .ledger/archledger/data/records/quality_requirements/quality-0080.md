---
schema_version: 4
id: quality-0080
kind: quality
type: quality_requirement
title: Type Safety (mypy strict)
status: accepted
section: quality_requirements
order: 60
version: 3
category: maintainability
source: ""
measure: ""
scenarios: []
body_format: markdown
---

All public interfaces must be fully type-annotated. mypy --strict must pass on the booktx package. Measurement: python -m mypy booktx exits 0.

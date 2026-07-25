---
schema_version: 4
id: quality-0081
kind: quality
type: quality_requirement
title: Lint Compliance (Ruff)
status: accepted
section: quality_requirements
order: 70
version: 3
category: maintainability
source: ""
measure: ""
scenarios: []
body_format: markdown
---

Code must pass Ruff linting with project configuration (.ruff.toml). Measurement: python -m ruff check . exits 0.

---
schema_version: 4
id: deploy-0058
kind: deploy
type: infrastructure
title: Development Environment
status: accepted
section: deployment_view
level: 1
parent: null
order: 10
version: 3
environment: development
maps_building_blocks:
  - block-0037
  - block-0038
  - block-0039
  - block-0040
  - block-0041
  - block-0042
  - block-0043
  - block-0044
  - block-0045
  - block-0046
  - block-0049
  - block-0050
body_format: markdown
---

Local development uses pip install -e .[dev,docs] with editable install. Dependencies managed via pyproject.toml. Testing via pytest, linting via Ruff, type checking via mypy --strict. Quality gate script runs the full validation pipeline locally.

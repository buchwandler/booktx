---
schema_version: 4
id: deploy-0059
kind: deploy
type: infrastructure
title: CI Environment (GitHub Actions)
status: accepted
section: deployment_view
level: 1
parent: null
order: 20
version: 3
environment: ci
maps_building_blocks:
  - block-0049
  - block-0050
body_format: markdown
---

GitHub Actions workflows: quality.yml enforces the quality gate on PRs and release branches for Python 3.10 and 3.13. python-publish.yml requires the quality gate to pass for the exact checked-out commit before publishing to PyPI. Build artifacts include the wheel and sdist.

---
schema_version: 4
id: deploy-0060
kind: deploy
type: infrastructure
title: PyPI Release
status: accepted
section: deployment_view
level: 1
parent: null
order: 30
version: 3
environment: production
maps_building_blocks:
  - block-0050
body_format: markdown
---

Release workflow: git tag triggers python-publish.yml. hatchling builds the wheel and sdist. The quality gate must pass for the tagged commit. Artifacts published to PyPI with exact commit evidence. Version derived from VCS via hatch-vcs.

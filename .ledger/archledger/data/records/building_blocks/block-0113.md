---
schema_version: 4
id: block-0113
kind: block
type: black_box
title: Quality Backends (quality_backends/)
status: accepted
section: building_block_view
level: 1
parent: null
order: 150
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - quality
  - backends
  - languagetool
body_format: markdown
---

Pluggable linguistic quality backends with a Protocol-based interface. Defines `LinguisticBackend` contract and `BackendFinding` data class for backend-neutral quality findings. Ships with `LocalLanguageToolBackend` adapter that shells out to a caller-configured LanguageTool CLI executable. Never downloads LanguageTool or silently falls back to another backend.

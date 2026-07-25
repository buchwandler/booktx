---
schema_version: 4
id: concept-0065
kind: concept
type: concept
title: Strict vs. Lazy Validation
status: accepted
section: cross_cutting_concepts
order: 40
version: 2
applies_to: []
body_format: markdown
---

booktx distinguishes two validation modes: Runtime/startup where malformed optional metadata emits one warning and falls back (CLI must never refuse to start due to cosmetic metadata issues); and Strict mode for tests/release where command_catalog.py strict validation and scripts/quality_gate.py catch all issues before release.

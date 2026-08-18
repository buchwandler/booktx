---
schema_version: 4
id: glossary-0120
kind: glossary
type: glossary_term
title: Quality Backend
status: accepted
section: glossary
order: 280
version: 6
term: Quality Backend
definition:
  A pluggable local linguistic checker implementing the LinguisticBackend
  Protocol. Returns backend-neutral BackendFinding objects with rule, message, severity,
  and category.
body_format: markdown
---

A pluggable local linguistic checker implementing the `LinguisticBackend` Protocol. Returns backend-neutral `BackendFinding` objects with rule, message, severity, and category. Ships with `LocalLanguageToolBackend`; custom backends can be added by implementing the protocol.

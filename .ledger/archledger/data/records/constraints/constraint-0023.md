---
schema_version: 4
id: constraint-0023
kind: constraint
type: constraint
title: Detected translation stores
status: accepted
section: architecture_constraints
order: 20
version: 4
category: technical
impact:
  New profiles use v3 per-chunk shards; existing profiles retain detected v1/v2/v3
  storage, with TranslationStoreV2 as the compatibility model
body_format: markdown
---

Describe the rationale and consequences of this constraint.

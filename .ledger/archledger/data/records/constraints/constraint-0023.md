---
schema_version: 4
id: constraint-0023
kind: constraint
type: constraint
title: Translation-store compatibility boundary
status: accepted
section: architecture_constraints
order: 20
version: 7
category: technical
impact:
  New profiles use the v3 shard store by default; existing profiles retain their
  detected backend until explicit migration, and TranslationStoreV2 remains the compatibility
  materialization model.
body_format: markdown
---

This constraint captures the canonical translation-store policy.

- New profiles default to the shard-based v3 `translation-store/` backend.
- Existing profiles keep their detected v1/v2/v3 backend until explicit migration.
- `TranslationStoreV2` remains the compatibility materialization model and supported legacy backend for migration, rollback, parity, and snapshot workflows.
- Ordinary workflows should depend on repository-native record and chunk operations rather than full-store compatibility materialization.

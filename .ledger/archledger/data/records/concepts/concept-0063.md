---
schema_version: 4
id: concept-0063
kind: concept
type: concept
title: Content-Addressed Hashing
status: accepted
section: cross_cutting_concepts
order: 20
version: 2
applies_to: []
body_format: markdown
---

Every config, context view, glossary binding, and task snapshot is SHA-256 hashed. These hashes travel with task records, version candidates, and review candidates. Validation compares stored hashes against current filesystem state to detect drift. Enables staleness detection, context drift detection, and review chain staleness verification.

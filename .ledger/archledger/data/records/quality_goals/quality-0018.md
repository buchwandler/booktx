---
schema_version: 4
id: quality-0018
kind: quality
type: quality_goal
title: Deterministic Reproducibility
status: accepted
section: introduction_and_goals
order: 20
version: 4
priority: 2
scenario: Two builds with identical inputs produce SHA-256 identical output files
body_format: markdown
---

Given identical source document, context, profile config, and protected names, booktx build must produce byte-for-byte identical output. All hashing uses SHA-256; all JSON serialization uses sort_keys=True for deterministic output. The source manifest records the extraction-time source SHA-256.

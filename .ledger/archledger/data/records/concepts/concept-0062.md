---
schema_version: 4
id: concept-0062
kind: concept
type: concept
title: Placeholder Protection
status: accepted
section: cross_cutting_concepts
order: 10
version: 2
applies_to: []
body_format: markdown
---

During extraction, booktx replaces non-translatable spans (proper names, HTML/XML tags) with deterministic tokens (**NAME_001**, **TAG_001**). The Placeholder model records the token-to-original mapping. The translation agent sees only placeholdered text; the build step restores originals verbatim. This guarantees that markup and protected names survive round-trip without agent awareness.

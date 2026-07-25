---
schema_version: 4
id: concept-0064
kind: concept
type: concept
title: Version Provenance
status: accepted
section: cross_cutting_concepts
order: 30
version: 2
applies_to: []
body_format: markdown
---

The TranslationVersionLedger records who produced each major version (actor, harness, model) and each subversion's context hash. TranslationCandidate and TranslationReviewCandidate models carry baseline refs, context view hashes, and config hashes. This creates a complete provenance trail from source to output.

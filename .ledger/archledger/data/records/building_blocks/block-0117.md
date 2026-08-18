---
schema_version: 4
id: block-0117
kind: block
type: black_box
title: Translation Quality Policy (translation_quality.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 190
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - quality
  - policy
  - agent-instructions
body_format: markdown
---

Shared first-pass translation quality policy and prompt primitives used by both translation and grammar-judge workflows. Defines `QualityMode` (protocol/basic/strict), `ResolvedSubmissionQualityPolicy`, German grammar checklist, and target-language checklist rendering. Keeps quality-mode semantics, policy identity, and agent instructions in one place to prevent drift between lint, acceptance, and generated prompts.

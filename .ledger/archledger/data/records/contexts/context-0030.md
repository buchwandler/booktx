---
schema_version: 4
id: context-0030
kind: context
type: context_interface
title: Coding Agent Integration
status: accepted
section: context_and_scope
order: 20
version: 4
context_kind: technical
partner: LLM / Human Translator
inputs:
  - TranslationTask JSON
  - TranslationReviewTask JSON
  - JudgeTask JSON
outputs:
  - Translated JSON submissions
  - Review JSON submissions
  - Judge decision JSON
channels:
  - CLI stdin/args
  - Filesystem (task and submission files)
body_format: markdown
---

Describe this context interface.

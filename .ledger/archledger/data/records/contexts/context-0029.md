---
schema_version: 4
id: context-0029
kind: context
type: context_interface
title: Source Document Input
status: accepted
section: context_and_scope
order: 10
version: 4
context_kind: business
partner: Author / Publisher
inputs:
  - Source document (.md or .epub)
outputs:
  - Extracted chunks (.booktx/chunks/)
  - Source manifest (.booktx/source-manifest.json)
channels:
  - Filesystem read
body_format: markdown
---

Describe this context interface.

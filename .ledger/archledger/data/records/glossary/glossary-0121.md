---
schema_version: 4
id: glossary-0121
kind: glossary
type: glossary_term
title: Validation Receipt
status: accepted
section: glossary
order: 290
version: 8
term: Validation Receipt
definition:
  A short-lived JSON receipt stored under translations/<profile>/validation-receipts/
  that records a successful staged translation validation. Allows translate submit
  to skip re-validation when the same file is resubmitted unchanged.
body_format: markdown
---

A short-lived JSON receipt stored under `translations/<profile>/validation-receipts/` that records a successful staged translation validation. Keyed by content hash, task ID, context view hash, glossary hash, and quality policy fingerprint. Allows `translate submit` to skip re-validation when the same file is resubmitted unchanged.

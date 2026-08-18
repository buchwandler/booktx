---
schema_version: 4
id: block-0118
kind: block
type: black_box
title: Validation Receipts (validation_receipts.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 200
version: 4
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags:
  - validation
  - receipts
  - staged
body_format: markdown
---

Short-lived receipts for successful staged translation validation. Generates content-addressed receipt keys bound to task ID, input SHA-256, context view hash, glossary hash, and quality policy fingerprint. Receipts are written to `translations/<profile>/validation-receipts/` and allow the submit command to skip re-validation when the same file is submitted again unchanged.

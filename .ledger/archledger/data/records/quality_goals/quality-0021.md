---
schema_version: 4
id: quality-0021
kind: quality
type: quality_goal
title: Provenance Auditability
status: accepted
section: introduction_and_goals
order: 50
version: 4
priority: 5
scenario:
  Stale review chain is detected when base target hash differs from stored
  base_target_sha256
body_format: markdown
---

Every TranslationCandidate carries version, baseline, context, and config hashes. Review candidates form a DAG rooted at a translation version with provenance chain validation. The TranslationVersionLedger records actor/harness/model per major version. Validation detects drift via hash comparison.

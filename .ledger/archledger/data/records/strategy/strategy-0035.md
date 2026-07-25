---
schema_version: 4
id: strategy-0035
kind: strategy
type: strategy_item
title: Versioning Model (major.minor + R-pass.run)
status: accepted
section: solution_strategy
order: 40
version: 2
drivers: []
constraints: []
related_adrs: []
body_format: markdown
---

Translation versions use major.minor refs (e.g., 1.1, 2.3). Major versions represent identity/harness/model changes tracked in the TranslationVersionLedger. Minor (subversion) bumps occur when context or baseline policy changes. Review candidates use R<pass>.<run> refs and form a derivation DAG rooted at a translation version.

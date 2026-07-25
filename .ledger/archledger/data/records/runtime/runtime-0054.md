---
schema_version: 4
id: runtime-0054
kind: runtime
type: runtime_scenario
title: Review Lifecycle
status: accepted
section: runtime_view
order: 20
version: 5
participants:
  - User/AI Agent
  - booktx CLI
  - Filesystem (store, review tasks)
trigger: review next command
result: TranslationReviewTask JSON delivered; on submit, review candidate upserted
body_format: markdown
---

1. Agent runs 'booktx review next --profile de_default --pass 1'. 2. booktx resolves ReviewPassConfig, selects records with missing/stale review for pass. 3. booktx computes review context window (N before + N after records). 4. booktx snapshots review context, writes immutable TranslationReviewTask. 5. Agent reviews and runs 'booktx review submit <task-id>' with review candidates. 6. booktx validates review chain (DAG acyclicity, pass order, base integrity). 7. On acceptance, booktx upserts TranslationReviewCandidate into store.

---
schema_version: 4
id: runtime-0053
kind: runtime
type: runtime_scenario
title: Translation Lifecycle
status: accepted
section: runtime_view
order: 10
version: 5
participants:
  - User/AI Agent
  - booktx CLI
  - Filesystem (store, context, tasks)
trigger: translate next command
result: TranslationTask JSON delivered; on submit, store updated with new version
body_format: markdown
---

1. Agent runs 'booktx translate next --profile de_default'. 2. booktx resolves profile, loads store + context, selects untranslated records. 3. booktx snapshots context view, glossary bindings, termbase entries, config hashes. 4. booktx writes immutable TranslationTask JSON with frozen context paths. 5. Agent translates records. 6. Agent runs 'booktx translate submit <task-id>' with translated JSON. 7. booktx validates submission against task snapshot, runs linguistic audit if configured. 8. On acceptance, booktx upserts TranslationCandidate versions into TranslationStoreV2.

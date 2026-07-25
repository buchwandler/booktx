---
schema_version: 4
id: content-0006
kind: content
type: section
section: runtime_view
title: Runtime View
order: 60
status: accepted
version: 2
body_format: markdown
---

## Primary Runtime Scenarios

### RS-1: Translation Lifecycle

1. Agent runs `booktx translate next --profile de_default` to get next pending task
2. booktx resolves profile, loads store + context, selects untranslated records
3. booktx snapshots context view, glossary bindings, termbase entries, config hashes
4. booktx writes immutable `TranslationTask` JSON with frozen context paths
5. Agent translates records and runs `booktx translate submit <task-id>` with translated JSON
6. booktx validates submission against task snapshot, runs linguistic audit if configured
7. On acceptance, booktx upserts `TranslationCandidate` versions into `TranslationStoreV2`

### RS-2: Review Lifecycle

1. Agent runs `booktx review next --profile de_default --pass 1`
2. booktx resolves `ReviewPassConfig`, selects records with missing/stale review for pass
3. booktx computes review context window (N before + N after records)
4. booktx snapshots review context, writes immutable `TranslationReviewTask`
5. Agent reviews and runs `booktx review submit <task-id>` with review candidates
6. booktx validates review chain (DAG acyclicity, pass order, base integrity)
7. On acceptance, booktx upserts `TranslationReviewCandidate` into store

### RS-3: Build Output

1. User runs `booktx build --profile de_default`
2. For each record, booktx resolves effective candidate:
   - Prefer active `TranslationReviewCandidate` if chain-valid
   - Fall back to active `TranslationCandidate`
3. booktx restores protected placeholders (`__NAME_NNN__`, `__TAG_NNN__`)
4. booktx assembles target document (Markdown or EPUB XHTML)
5. Output written to `translations/<profile>/output/`

### RS-4: Judge Workflow

1. User runs `booktx judge task-next --profile judge_profile --sources sourceA sourceB`
2. booktx loads source store and selection profiles, builds `JudgeTask`
3. Agent evaluates candidates and runs `booktx judge submit <task-id>` with decisions
4. booktx records decisions in `selection-ledger.json`

## Error Handling Runtime

On CLI startup, `booktx.bootstrap.main()` wraps `booktx.cli.main()` in a try/except. Import errors render a concise message with exit code 70 and `BOOKTX_DEBUG=1` opt-in for full tracebacks. Malformed command catalog metadata emits one warning and falls back to native Typer help; strict validation is reserved for explicit test/release checks.

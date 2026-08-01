# Translation store policy

The translation store is profile-local canonical state. New profiles use v3;
existing profiles keep the backend detected from disk. Ordinary loading,
validation, build, status, and acceptance commands never migrate an existing
profile implicitly.

## Backends and detection

V3 is the directory `translations/<profile>/translation-store/`:

```text
translation-store/
  manifest.json
  current/0001.json
  translation-candidates/0001.json
  review-candidates/0001.json
  transactions/
```

Each file is per source chunk. The manifest is authoritative for the chunk
set. If the v3 directory has a valid manifest, it wins detection. Otherwise a
`translation-store.json` with version 2 is the canonical v2 backend. A missing
store is created only by the explicit creation policy: v3 for a new profile,
or v2 through `booktx profile create --store-format v2`.

## Consistency and recovery

Every changed chunk writes the same advancing revision to its current,
translation-candidate, and review-candidate envelopes. A reader obtains all
three files inside a bounded revision boundary, validates source hashes and
candidate-selection invariants, and retries if a writer is publishing. Writers
use staged transaction journals, optimistic file hash/revision checks, a
store-root lock, and roll-forward recovery after interruption.

The doctor inventories the manifest and all shard directories. It reports
missing, orphan, incomplete, unexpected, revision-mismatched, invalid, and
pending-transaction state without repairing it. Run:

```bash
booktx translate store-status ./book --profile PROFILE
booktx translate store-status ./book --profile PROFILE --json
```

## Migration and rollback

Migration is explicit and dry-run first:

```bash
booktx translate migrate-store ./book --profile PROFILE --to v3 --json
booktx translate migrate-store ./book --profile PROFILE --to v3 --write
booktx translate migrate-store ./book --profile PROFILE --to v2 --write
```

Existing profiles are not auto-migrated. A preserved v2 file may coexist with
v3 after `--keep-legacy-copy`; v3 remains canonical and store-status reports
the legacy copy and its hash. Rollback flattens v3 into the v2 compatibility
model, so v3-only layout metadata should be treated as operational provenance,
not record content.

## Compatibility and promotion gate

`TranslationStoreV2` remains the compatibility in-memory model used for
migration, export, and APIs that need the legacy shape. The executable
readiness gate covers v2/v3 validation parity, consistent reads, revisions,
provenance, doctor inventory, workflow parity, recovery, store-status, default
policy, documentation, and available scalability checks. The quality gate runs
the focused store suite, parity suite, operational checks, full tests, and
packaging checks.

# Maintenance

Maintenance commands handle migration, diagnostics, generated exports, and
low-level storage. They are separate from the human-first workflow and may be
hidden from the default help panels.

## Diagnostics

```bash
booktx mode .
booktx doctor isolation .
booktx epub inspect ./book --profile PROFILE
booktx qa-scan ./book --profile PROFILE
```

## Legacy migration

```bash
booktx profile migrate-current ./book PROFILE
booktx translate import-legacy ./book --profile PROFILE
booktx translate migrate-store ./book --profile PROFILE
booktx translate migrate-inline-xhtml ./book --profile PROFILE
```

Use migration commands only for legacy projects. New profiles use v3 by
default; existing profiles retain their detected backend. Inspect first with
`booktx translate store-status ./book --profile PROFILE`, then use the
dry-run-first migration command when changing an existing profile:
`booktx translate migrate-store ./book --profile PROFILE --to v3 --write`.

## Generated exports and storage

```bash
booktx translate export ./book --profile PROFILE
booktx translate export-index ./book --profile PROFILE
booktx termbase status ./book --profile PROFILE
booktx termbase export ./book --profile PROFILE
```

Generated exports and indexes can be regenerated. Do not edit the profile store
or generated files directly.

## Recovery boundaries

`booktx pass-through` is a generated reconstruction check. Version selection and
record corrections must use the current `translate`, `review`, or `judge`
workflow that owns the relevant provenance. Run the exact command help before
using maintenance operations.

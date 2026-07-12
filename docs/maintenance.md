# Maintenance

These commands are available for migration, diagnostics, and low-level repair.
They are intentionally hidden from the default root help.

## Diagnostics

```bash
booktx doctor isolation .
booktx mode .
```

## Legacy migration

```bash
booktx profile migrate-current ./book PROFILE
booktx translate import-legacy ./book --profile PROFILE
booktx translate migrate-store ./book --profile PROFILE
booktx translate migrate-inline-xhtml ./book --profile PROFILE
```

## Low-level storage

```bash
booktx termbase status ./book --profile PROFILE
booktx termbase export ./book --profile PROFILE
booktx termbase import ./book --profile PROFILE --input termbase.json
```

## Emergency rebuild helpers

```bash
booktx pass-through ./book --profile PROFILE
booktx version select ./book --profile PROFILE 1.2
```

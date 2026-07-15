# Concepts

## Source project

The source project is shared by all profiles:

- `source/`
- `.booktx/source-config.toml`
- `.booktx/source-manifest.json`
- `.booktx/names.json`
- `.booktx/chapter-map.json`
- `.booktx/chunks/`

Re-extraction updates this shared source state for every profile.

## Translation profile

A profile is an isolated translation effort under
`translations/<profile>/`. It owns target language and locale, identity
configuration, context, the translation store, version ledger, tasks, ingest
files, reviews, reports, and rebuilt output.

Project-root commands require an explicit `--profile PROFILE` when they read or
write profile-local data. A command run from a profile root resolves the profile
from the validated `.booktx-profile.json` marker and uses `.` as its project
argument. There is no project-wide selector and no implicit single-profile
resolution.

## State of truth

New profiles currently use `TranslationStoreV2` as the canonical record store.
When a profile opts into v3, `translations/<profile>/translation-store/`
becomes the canonical shard-based backend. `TranslationStoreV2` remains the
compatibility materialization model used by the loader surface.
`translation-version-ledger.json` records version history. Generated
`translated/`, editor indexes, reports, and output files are derived artifacts
and can be rebuilt.

`translations/<profile>/context.json` is authoritative context state.
`context.md` is its rendered view. Effective context views used by tasks are
snapshotted under `context-history/views/<sha>/`.

## Versions and reviews

Versions are scoped inside one profile. A model or baseline policy change can
create a new dotted version. A chapter note changes the next task's effective
context but does not create a dotted version by itself. Review candidates use
the separate `R<pass>.<run>` namespace and are selected only when their
provenance chain is valid.

## Context and terminology

Context is profile-local. To reuse approved policy between books, use an
explicit context pack. To align sibling profiles in one book, use `context sync`; each target keeps its own context files.

Use `booktx glossary` for human terminology decisions. Use `booktx termbase`
only for the advanced reusable preference storage surface.

# Profiles

Profiles isolate mutable translation state while sharing extracted source data.
Create one for each target language, model experiment, or deliberately
separate context decision.

## Project-root commands

```bash
booktx profile create ./book PROFILE_A --target de --target-locale de-DE
booktx profile list ./book
booktx profile show ./book PROFILE_A
booktx profile compare ./book --profiles PROFILE_A,PROFILE_B --record 0001-000001
```

A project-root command that needs profile-local data must receive
`--profile PROFILE`. There is no global profile selector and target-state
commands do not infer a profile from the number of profiles in the project.

## Profile-root commands

A validated profile root is `translations/<profile>/`. Its
`.booktx-profile.json` marker binds the directory to the profile configuration,
project root, target locale, and current source identity. From that directory,
use `.` and omit `--profile`:

```bash
cd translations/PROFILE_A
booktx mode .
booktx doctor isolation .
booktx source status .
booktx context status .
booktx translate next . --unit batch --max-words 800 --format block
booktx validate .
booktx build .
```

Profile-root mode exposes only the selected profile through booktx's brokered
source commands. It is booktx-mediated isolation, not an operating-system
sandbox. Parent paths, sibling profiles, and arbitrary filesystem inspection
are outside the isolated workflow.

## Shared and local state

`.booktx/` contains shared source-derived state:

- `source-config.toml`, `source-manifest.json`
- `names.json`, `chapter-map.json`, and `chunks/`
- source-analysis evidence and shared reports

`translations/<profile>/` contains profile-local mutable state:

- `.booktx-profile.json`, `config.toml`, and `identity.json`
- `context.json`, `context.md`, and `context-history/`
- `translation-store/` and `translation-version-ledger.json`
- `tasks/`, `todos/`, `ingest/`, `reviews/`, `review-todos/`, and judge artifacts
- generated `translated/`, indexes, `reports/`, and `output/`

Never use sibling profile paths in an isolated profile-root workflow. Never edit
canonical store shards or generated exports directly; use the CLI surfaces.

## Context transfer

For a different book, export and import a context pack. For sibling profiles in
the same book, use project-root `booktx context sync`. These operations copy
approved policy into each profile; they do not share mutable stores or context
files.

## Pass-through profiles

Pass-through profiles are generated reconstruction checks. They use source text
as target text and must not be used for human translation:

```bash
booktx profile create-pass-through ./book passthrough_en
booktx validate ./book --profile passthrough_en
booktx build ./book --profile passthrough_en
```

## Selection and revision profiles

`booktx judge create-profile` creates compare or revision profiles. A selection
profile stores accepted judge decisions in its normal `TranslationStoreV2`
store and keeps judge provenance separately. A single-source revision profile
requires an explicit `copy` or `edited` judge decision for every record.
Prepare isolated judge work from the project root with
`booktx judge prepare-isolation`, then continue from the profile root.

## Versions

Versions live inside one profile. Translation versions use dotted references,
while review candidates use `R<pass>.<run>` references. Compare or activate
versions with:

```bash
booktx translate compare . --profile PROFILE_A RECORD --versions 1.1,1.2
booktx translate activate . --profile PROFILE_A RECORD 1.2
```

## Legacy migration

Legacy single-layout projects may be migrated once:

```bash
booktx profile migrate-current ./book PROFILE_A
```

After migration, `.booktx/` remains the shared source tree and mutable
translation state moves under `translations/PROFILE_A/`.

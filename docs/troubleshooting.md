# Troubleshooting

## Multiple profiles or missing profile

Project-root commands that read or write profile-local data require an explicit
profile:

```bash
booktx profile list ./book
booktx status ./book --profile PROFILE
booktx guide ./book --profile PROFILE
```

If no profile exists, create one:

```bash
booktx profile create ./book PROFILE --target de --target-locale de-DE
```

From `translations/PROFILE/`, use `.` and omit `--profile`. If the marker is
missing, mismatched, or stale, regenerate the profile-root instructions or
repair the profile through the project-root workflow before isolated work.

## Profile and submission mismatches

A task or submission created for another profile cannot be inserted into the
selected profile. Request a fresh task and use the matching profile-local
`ingest/` file:

```bash
booktx translate next ./book --profile PROFILE --format block
```

Do not edit `translation-store.json` to work around a mismatch.

## Legacy paths after migration

After `booktx profile migrate-current`, mutable translation state belongs under
`translations/<profile>/`. Old `.booktx/` paths such as `context.json`,
`tasks/`, `ingest/`, `translated/`, and `translation-store.json` are legacy
migration input, not current profile storage.

## Stale tasks and context

If insertion reports stale task metadata, request a new task after the context,
glossary, source, or version change:

```bash
booktx context status ./book --profile PROFILE
booktx translate next ./book --profile PROFILE --format block
```

`context.json` is authoritative. If `context.md` contains manual notes, import
or replace them with `booktx context import-md` before rendering. Do not mark
context ready until the user has approved required questions.

## Source drift and missing chunks

Re-extract after an intentional source change or when chunks are missing:

```bash
booktx extract ./book
booktx chapters ./book --audit
```

The source checksum and chapter audit must be current before new tasks are
created. An extracted EPUB target with no chapter-map boundary is an error;
warning-only preview or navigation findings remain visible for review.

## Validation and build

Use scoped checks during bounded work and the full validation before output:

```bash
booktx check ./book --profile PROFILE --fail-on-warnings
booktx validate ./book --profile PROFILE --fail-on-warnings
booktx build ./book --profile PROFILE --require-complete
```

If EPUB validation reports an inline-XHTML finding, preserve the source tag and
attribute skeleton and change only text nodes. The same preflight is used by
validation and build.

If an output filename does not match the profile target, update the profile
configuration rather than renaming generated files by hand.

## Context and series preparation

`booktx series prepare` is a project-root workflow. Provide exactly one policy
source, either `--from-book` or `--pack`, then review the generated questionnaire
before `context mark-ready`. Do not run series preparation from a profile root.

## EPUB output policy

Target language metadata and generated hyphenation CSS are controlled by the
profile's `[epub_output]` policy. Automatic hyphenation still depends on the
reader. Set `hyphenation = "none"` when the reader produces unacceptable
breaks, then rebuild.

## Bounded todos

Inspect an incomplete run before requesting more work:

```bash
booktx translate todo-status ./book --profile PROFILE --latest
booktx translate todo-resume ./book --profile PROFILE --latest --format block
```

When planned chapters are complete, create a new bounded todo with
`translate todo-next`. Keep todo files as run-control artifacts, not submission
files.

## Glossary and termbase

Use `booktx glossary` for binding terminology decisions and `booktx termbase`
for advanced reusable preferences. After a mandatory glossary change, request
fresh translation tasks and audit the effective output. Longer glossary phrases
shadow contained shorter matches; do not force an unnatural compound to satisfy
a shorter rule.

## Judge ingest

For a corrupted judge ingest file, regenerate it from the stored task:

```bash
booktx judge reset-ingest ./book --profile PROFILE \
  --judge-task-id TASK --format decisions --write
```

For revision profiles, every record requires an explicit `copy` or `edited`
decision. Later corrections use judge commands, not direct store edits.

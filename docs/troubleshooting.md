# Troubleshooting

## `multiple translation profiles exist`

Pass `--profile` or select one first:

```bash
booktx profile select ./book de_gpt5_5
```

## `no translation profile exists`

Create one:

```bash
booktx profile create ./book de_gpt5_5 --target de
```

## `task profile mismatch`

The task was created for another profile. Request a fresh task in the selected
profile.

## `submission profile mismatch`

The durable submission file or JSON payload declares a different profile than
the selected one. Use the matching `translations/<profile>/ingest/` file.

## `output filename ... does not match target language ...`

Choose an output filename that matches the profile target, for example
`book.de.epub`.

## `legacy path used after migration`

After migrating, do not use:

- `.booktx/context.json`
- `.booktx/context.md`
- `.booktx/tasks/`
- `.booktx/ingest/`
- `.booktx/translated/`
- `.booktx/translation-store.json`

Use the selected profile paths under `translations/<profile>/` instead.

## Stale translation task version

`booktx translate insert` reports a stale task version when the durable task
was created against an older context/version than the current one. Do not
force the old file through. Request a fresh task:

```bash
booktx translate next ./book --profile PROFILE --format block
```

and submit the newly generated ingest file.

## Source drift after extraction

If the source file changed since the last `booktx extract`, the recorded
source hash no longer matches and inserts/builds are blocked. Re-extract to
realign the chunks and source manifest:

```bash
booktx extract ./book
```

Then re-request tasks against the refreshed source.

## Context is not ready

Translation work requires a ready context. If you see `translation context is
missing or not ready`, initialize and mark it ready:

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx context mark-ready ./book --profile PROFILE --force
```

## Missing source chunks

`No source chunks found` means extraction has not run (or the source file is
missing). Check `.booktx/source-config.toml` points at an existing source
file, then:

```bash
booktx extract ./book
```

## Output filename mismatch

The output filename must match the profile target language. For Markdown the
rebuilt file is `translations/<profile>/output/<name>.md`; for EPUB it is
`translations/<profile>/output/<name>.epub`. If `booktx build` complains,
create a profile with a matching `--target`/output filename, or override with
`booktx profile create ... --output-filename book.de.md`.

## Old `.booktx/ingest` path after migration

After `booktx profile migrate-current`, submissions belong under
`translations/<profile>/ingest/`, never `.booktx/ingest/`. If a missing-file
error hints at the profile-local ingest path, switch to that file. Re-running
`booktx translate next` regenerates the correct ingest file.

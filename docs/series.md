# Series workflows

Use `booktx series prepare` for the normal "start the next book in the same
translated series" path.

## Normal path

```bash
booktx series prepare ./book5 \
  --source-file ./book5/book5.epub \
  --from-book ./book4 \
  --from-profile de_glm_5_2 \
  --profile de_glm_5_2 \
  --series-id shadows-of-the-apt \
  --title "Shadows of the Apt German series context" \
  --target de \
  --target-locale de-DE \
  --model zai/glm-5.2@high \
  --write \
  --write-termbase \
  --termbase-scope project
```

The command:

1. Initializes or reuses the source project.
2. Extracts source chunks when needed.
3. Runs the EPUB chapter audit.
4. Creates or reuses the target profile.
5. Imports the previous book's reusable context policy.
6. Runs source analysis and refreshes profile snapshots.
7. Prefills context review work and renders `context.md`.
8. Builds the profile-local source-interview ledger, Markdown/JSON report, and
   hash-bound decision template.
9. Writes `.booktx/reports/series-prepare.json` and `.md`.
10. Stops before translation and before automatic readiness mutation.

Review and finalize handoff:

```bash
booktx source interview-report ./book5 --profile de_glm_5_2 --write
# Review/edit .booktx/reports/source-interview-decisions.json.
booktx source interview-apply ./book5 --profile de_glm_5_2 \
  --file .booktx/reports/source-interview-decisions.json --write
booktx series finalize ./book5 --profile de_glm_5_2 --write
```

For an existing or partially prepared book, use the idempotent project-root
workflow `booktx series review ./book5 --profile PROFILE --write` and inspect
`booktx series status ./book5 --profile PROFILE`.

## Pack mode

Use `--pack` when you already exported a series context pack:

```bash
booktx series prepare ./book5 \
  --source-file ./book5/book5.epub \
  --pack ./series-context.de.json \
  --profile de_glm_5_2 \
  --series-id shadows-of-the-apt \
  --title "Shadows of the Apt German series context" \
  --target de \
  --target-locale de-DE \
  --model zai/glm-5.2@high \
  --write
```

## Recipes

Write a reusable recipe once:

```bash
booktx series recipe write ./book5 \
  --profile de_glm_5_2 \
  --series-id shadows-of-the-apt \
  --title "Shadows of the Apt German series context" \
  --output ../shadows-of-the-apt.de.booktx-series.toml
```

Then prepare the next book with fewer flags:

```bash
booktx series prepare ./book6 \
  --source-file ./book6/book6.epub \
  --from-book ./book5 \
  --recipe ../shadows-of-the-apt.de.booktx-series.toml \
  --write
```

## Manual path

The manual `context export-pack` / `init` / `extract` / `profile create` /
`context import-pack` / `source analyze` / `context prefill` workflow still
works unchanged when you want full step-by-step control.

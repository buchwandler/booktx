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
8. Writes `.booktx/reports/series-prepare.json` and `.md`.
9. Stops before translation and before automatic `context mark-ready`.

Review handoff:

```bash
booktx context questionnaire ./book5 --profile de_glm_5_2 --stdout
booktx context status ./book5 --profile de_glm_5_2
booktx context render ./book5 --profile de_glm_5_2 --write
booktx context mark-ready ./book5 --profile de_glm_5_2
booktx agents write ./book5 --mode isolated --profile de_glm_5_2
```

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

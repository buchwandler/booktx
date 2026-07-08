# Quickstart

## 1. Initialize a source project

```bash
booktx init ./demo --source-file book.epub --source-lang en
```

## 2. Extract the source

```bash
booktx extract ./demo
```

## 3. Create and select a translation profile

```bash
booktx profile create ./demo PROFILE_A \
  --target de \
  --target-locale de-DE \
  --model codex-openai/gpt-5.5@low \

```

## 4. Initialize the profile-local context

```bash
booktx context init ./demo --profile PROFILE_A --non-interactive
booktx context questions ./demo --profile PROFILE_A
# Ask the user to approve or edit answers before continuing.
booktx context approve ./demo --profile PROFILE_A Q001 --text "<USER_APPROVED_TEXT>" --approved-by "user:<USER>"
booktx context render ./demo --profile PROFILE_A --write
booktx context mark-ready ./demo --profile PROFILE_A
```

## 5. Request a translation task

```bash
booktx translate next ./demo --profile PROFILE_A --unit batch --max-words 800 --format block
```

Read `translations/PROFILE_A/context.md`, then fill the generated durable file
under `translations/PROFILE_A/ingest/`.

## 6. Submit the translation

```bash
booktx translate insert ./demo \
  --profile PROFILE_A \
  --task-id TASK \
  --file translations/PROFILE_A/ingest/TASK.block.txt \
  --format block
```

## 7. Validate and build

```bash
booktx validate ./demo --profile PROFILE_A
booktx build ./demo --profile PROFILE_A
```

The rebuilt output is written under:

```text
demo/translations/PROFILE_A/output/
```

## Legacy projects

Old single-layout projects can be migrated with:

```bash
booktx profile migrate-current ./demo PROFILE_A
```

## Context approval

booktx never decides translation policy by itself. An agent may propose context answers, but the user must approve them before translation begins. Do not use `context mark-ready --force` during normal translation work.

## Next book in a series

Normal path:

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

Then review the generated context and finish the human gate:

```bash
booktx context questionnaire ./book5 --profile de_glm_5_2 --stdout
booktx context status ./book5 --profile de_glm_5_2
booktx context render ./book5 --profile de_glm_5_2 --write
booktx context mark-ready ./book5 --profile de_glm_5_2
booktx agents write ./book5 --mode isolated --profile de_glm_5_2
```

Advanced/manual path:

1. Export a context pack from the completed profile.
2. Initialize and extract the new book, then create the matching profile.
3. Run `booktx context import-pack` as a dry run, then re-run with `--write`.
4. Run `booktx source analyze --write --sync-profiles`.
5. Run `booktx context prefill --from-source-analysis --consolidate-imported-policy --write`.
6. Review the context questionnaire, then mark ready and write isolated agent instructions.

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

1. Check the completed source profile: `booktx status BOOK3` and `booktx context status BOOK3 --profile PROFILE`.
2. Export a context pack from the completed profile.
3. Initialize and extract the new book, then create the matching profile.
4. Run `booktx context import-pack BOOK --profile PROFILE --file PACK --init-missing-context --conflict fail` as a dry run.
5. Re-run with `--write`; add `--write-termbase --termbase-scope project` only when reusable termbase entries should be imported.
6. Render context, print the questionnaire, and wait for human approval before `context mark-ready`.
7. Run `booktx source analyze BOOK --write --sync-profiles`, prefill context from source analysis, review any new questions, then mark ready.
8. Write isolated-agent instructions and start translation only from the profile root.

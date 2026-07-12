# Commands

This reference follows the **human-first** CLI surface. Use `booktx guide` to
discover the exact next step for a real project state.

There is **no global profile selection**. From the project root, pass
`--profile PROFILE` for profile-specific work. From `translations/PROFILE/`,
the current profile root resolves `PROFILE` without exposing siblings.

## Start or resume

```bash
booktx guide ./book --profile PROFILE
booktx status ./book --profile PROFILE
```

`guide` is the canonical "what do I do next?" entry point. `status` adds
progress plus the current human and agent actions.

## Book setup

```bash
booktx init ./book --source-file ./book.epub --source-lang en
booktx extract ./book
booktx chapters ./book --audit
booktx profile create ./book PROFILE --target de --target-locale de-DE --model MODEL
booktx profile list ./book
booktx profile show ./book PROFILE
```

Advanced profile inspection remains available:

```bash
booktx profile compare ./book --profiles PROFILE_A,PROFILE_B --record 0001-000001
booktx profile migrate-current ./book PROFILE
booktx profile create-pass-through ./book passthrough_en
```

## Human decisions and context

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx source analyze ./book --write --sync-profiles
booktx source interview-plan ./book --profile PROFILE --write
booktx source interview-status ./book --profile PROFILE
booktx source interview-next ./book --profile PROFILE --format markdown
booktx context questionnaire ./book --profile PROFILE --stdout
booktx context approve ./book --profile PROFILE Q001 --text "..." --approved-by "user:<USER>"
booktx context mark-ready ./book --profile PROFILE
```

`context recommend` remains available for agent protocol work, but it is not the
default human command.

## Glossary

Use `glossary` as the canonical human terminology surface:

```bash
booktx glossary status ./book --profile PROFILE
booktx glossary add ./book "Moth-kinden" --profile PROFILE --target "Mottenartige"
booktx glossary mandate ./book "Empire" --profile PROFILE --target "Imperium" --forbid "Reich"
booktx glossary reset ./book "Empire" --profile PROFILE --target "Imperium" --require-target
booktx glossary remove ./book "Empire" --profile PROFILE
booktx glossary audit ./book "Empire" --profile PROFILE
booktx glossary export ./book --profile PROFILE --scope effective --output glossary.json
booktx glossary import ./book --profile PROFILE --input glossary.json --mode merge
```

`termbase` remains available as the advanced storage/schema surface.

## Isolated agent workspace

```bash
booktx agents write ./book --mode isolated --profile PROFILE
booktx agents status ./book
booktx agents clean ./book --mode isolated --profile PROFILE
```

After writing isolated instructions, start the harness inside
`translations/PROFILE/`.

## Series workflow

Recipe-first series preparation is the normal documented path:

```bash
booktx series recipe write ./book4 \
  --profile de_glm_5_2 \
  --series-id shadows-of-the-apt \
  --title "Shadows of the Apt German series context" \
  --output ./shadows-of-the-apt.de.booktx-series.toml

booktx series prepare ./book5 \
  --source-file ./book5.epub \
  --from-book ./book4 \
  --recipe ./shadows-of-the-apt.de.booktx-series.toml \
  --write
```

## Quality workflows

```bash
booktx review configure ./book --profile PROFILE
booktx review status ./book --profile PROFILE
booktx judge create-profile ./book judge_de --target de --sources PROFILE --model MODEL
booktx judge prepare-isolation ./book --profile judge_de --write
booktx judge prepare-grammar ./book --source-profile PROFILE --profile judge_de_grammar --model MODEL --write
booktx judge status ./book --profile judge_de
```

The record-by-record `review` and `judge` task commands remain available for the
agent workflow but are not the normal human starting point.

## Build and advanced inspection

```bash
booktx check ./book --profile PROFILE
booktx build ./book --profile PROFILE
booktx validate ./book --profile PROFILE
booktx qa-scan ./book --profile PROFILE
booktx epub inspect ./book --profile PROFILE
booktx version current ./book --profile PROFILE
booktx whoami ./book --profile PROFILE
booktx identity set ./book --profile PROFILE --actor user:NAME --harness codex --model MODEL
```

## Agent protocol commands

The `translate`, `review`, and `judge` task commands remain available for the
coding-agent harness. Use `booktx translate` in new documentation:

```bash
booktx translate next ./book --profile PROFILE --unit batch --max-words 800 --format block
booktx translate insert ./book --profile PROFILE --task-id TASK --file translations/PROFILE/ingest/TASK.block.txt --format block
booktx translate get-record ./book --profile PROFILE 74@38 --json
booktx translate compare ./book --profile PROFILE 74@38 --versions 1.1,1.2
booktx translate revise-record ./book --profile PROFILE 74@38 --target "Revised target"
booktx translate revise-block ./book --profile PROFILE --file ingest/fixes.block.txt --format block --activate
booktx translate search ./book --profile PROFILE --target "Wespen" --before 1 --after 1
booktx translate todo-next ./book --profile PROFILE --chapters 3 --batch-words 800 --write
booktx translate todo-status ./book --profile PROFILE --latest
booktx translate todo-resume ./book --profile PROFILE --latest --format block
```

## Maintenance and recovery

These commands are intentionally hidden from the default root help, but they
remain available when you know you need them:

```bash
booktx doctor isolation .
booktx mode .
booktx termbase status ./book --profile PROFILE
booktx pass-through ./book --profile PROFILE
```

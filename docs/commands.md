# Commands

This is the human-first CLI reference. Run `booktx guide PROJECT --profile PROFILE` to get the next lifecycle action for a real project.

Project-root commands use an explicit `--profile PROFILE` for profile-local
state. A command run inside `translations/PROFILE/` uses `.` and the validated
profile marker to resolve that profile.

## Human lifecycle

```bash
booktx init ./book --source-file ./book.epub --source-lang en
booktx extract ./book
booktx chapters ./book --audit
booktx profile create ./book PROFILE --target de --target-locale de-DE --model MODEL
booktx guide ./book --profile PROFILE
booktx status ./book --profile PROFILE
booktx check ./book --profile PROFILE
booktx build ./book --profile PROFILE
```

Use `booktx inspect` for a read-only pre-extraction estimate and
`booktx qa-scan` for advanced target checks. `booktx validate` is the detailed
validation command. `booktx epub inspect` is the EPUB output inspection
surface.

## Human decisions

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx source analyze ./book --write --sync-profiles
booktx source interview-plan ./book --profile PROFILE --write
booktx source interview-next ./book --profile PROFILE --format markdown
booktx context questionnaire ./book --profile PROFILE --stdout
booktx context approve ./book --profile PROFILE Q001 --text "..." --approved-by "user:NAME"
booktx context mark-ready ./book --profile PROFILE
booktx glossary status ./book --profile PROFILE
booktx glossary mandate ./book "Empire" --profile PROFILE --target "Imperium" --forbid "Reich"
booktx glossary add-variant ./book "Beetle-kinden" --profile PROFILE --target "Käferartige" --usage vocative
booktx glossary set-usage ./book "Dragonfly-kinden" --profile PROFILE --person-singular "Angehörige der Libellenart"
booktx identity set ./book --profile PROFILE --actor user:NAME --harness codex --model MODEL
```

Recommendations and generated questionnaires are not user approval. Stop and
wait for explicit approval before `context approve` and `context mark-ready`.
Use `booktx glossary` for normal terminology decisions. The compatibility
`context *-term` commands and `termbase` storage commands are advanced surfaces.

## Profiles and isolation

```bash
booktx profile list ./book
booktx profile show ./book PROFILE
booktx profile compare ./book --profiles PROFILE_A,PROFILE_B --record 0001-000001
booktx agents write ./book --mode isolated --profile PROFILE
booktx agents status ./book
booktx profile migrate-current ./book PROFILE
booktx profile create-pass-through ./book passthrough_en
```

For isolated profile-root work:

```bash
cd translations/PROFILE
booktx mode .
booktx doctor isolation .
booktx source status .
booktx context status .
booktx validate .
booktx build .
```

## Series and quality workflows

```bash
booktx series prepare ./book5 --source-file ./book5.epub \
  --from-book ./book4 --profile PROFILE --series-id series-id \
  --title "Series policy" --target de --target-locale de-DE --model MODEL --write
booktx series recipe write ./book5 --profile PROFILE \
  --series-id series-id --title "Series policy" --output ./series.toml
booktx review configure ./book --profile PROFILE
booktx review status ./book --profile PROFILE
booktx judge create-profile ./book JUDGE --target de --sources PROFILE --model MODEL
booktx judge prepare-isolation ./book --profile JUDGE --write
booktx judge prepare-grammar ./book --source-profile PROFILE --profile JUDGE --model MODEL --write
booktx judge status ./book --profile JUDGE
booktx judge todo-next ./book --profile JUDGE --from-chapter 0001 --through-chapter 0010 \
  --batch-records 40 --batch-sentences 60 --batch-words 1800 --write --resume
booktx judge todo-status ./book --profile JUDGE --latest --json
booktx judge todo-resume ./book --profile JUDGE --latest --format decisions
booktx judge lint-decisions ./book --profile JUDGE --judge-task-id TASK \
  --file judge-ingest/TASK.decisions.txt --format decisions
booktx judge audit-copies ./book --profile JUDGE --task-id TASK --chapter 0001
```

`series prepare` stops for the human context review. `review` configures and
monitors quality passes. `judge` prepares comparison or revision profiles.

## Agent protocol

`translate` is the coding-agent task namespace. It is not the normal human
starting point:

```bash
booktx translate next ./book --profile PROFILE --unit batch --max-words 800 --format block
booktx translate insert ./book --profile PROFILE --task-id TASK \
  --file translations/PROFILE/ingest/TASK.block.txt --format block
booktx translate lint-block ./book --profile PROFILE --file ingest/TASK.block.txt --format block
booktx translate todo-next ./book --profile PROFILE --chapters 3 --batch-words 800 --write
booktx translate todo-status ./book --profile PROFILE --latest
booktx translate todo-resume ./book --profile PROFILE --latest --format block
booktx translate get-record ./book --profile PROFILE RECORD --json
booktx translate compare ./book --profile PROFILE RECORD --versions 1.1,1.2
booktx translate revise-record ./book --profile PROFILE RECORD --target "Revised target"
booktx translate revise-block ./book --profile PROFILE --file ingest/fixes.block.txt --format block --activate
booktx translate search ./book --profile PROFILE --target "Wespen" --before 1 --after 1
booktx translate concordance ./book --profile PROFILE --task-id TASK --auto --json
booktx translate todo-doctor ./book --profile PROFILE --overlaps
```

The agent workflow requires approved context, bounded tasks, unchanged record
headers, and preserved placeholders. It must not edit the store directly.
Agent-only review and judge task commands are documented in
[agent workflow](agent-workflow.md).

## Advanced and maintenance surfaces

These commands are intentionally separate from the human happy path:

```text
version current/list/show/select/set-label/fork-context
whoami
mode
inspect
qa-scan
termbase status/add/validate-entry/export/import/scan-source/audit/
  promote-candidate/promote-context/write-review
translate export/export-index/list/activate/review/set-record/
  import-legacy/migrate-store/audit-inline/migrate-inline-xhtml/task-status
judge sync-sources/next/continue/record/show/insert/reset-ingest/
  accept-identical/sweep-identical/prefill-policy-fixes/finish-chapter-plan
context doctor/render/audit-term/export-pack/import-pack/sync/recommend/answer/import-md
source record/chapter
```

Use the matching `--help` output before running an advanced or maintenance
command. The old top-level `translation`, `model`, `actor`, and `harness`
namespaces are not part of the current CLI.

## Public human command paths

> The following paths are part of the current Typer tree. Use `--help` to
> confirm arguments and flags before copying an example.

```text
booktx agents
booktx agents clean
booktx agents status
booktx agents write
booktx build
booktx chapters
booktx check
booktx context
booktx context add-question
booktx context add-term
booktx context approve
booktx context audit-term
booktx context chapter-note
booktx context doctor
booktx context export-pack
booktx context import-pack
booktx context init
booktx context mandate-term
booktx context mark-ready
booktx context prefill
booktx context promote-candidate
booktx context questionnaire
booktx context questions
booktx context remove-term
booktx context render
booktx context reset-term
booktx context status
booktx context sync
booktx doctor isolation
booktx epub
booktx epub extract-text
booktx epub grep
booktx epub inspect
booktx extract
booktx glossary
booktx glossary add
booktx glossary add-variant
booktx glossary audit
booktx glossary export
booktx glossary import
booktx glossary mandate
booktx glossary remove
booktx glossary reset
booktx glossary set-usage
booktx glossary status
booktx guide
booktx identity
booktx identity clear
booktx identity set
booktx init
booktx inspect
booktx judge
booktx judge create-profile
booktx judge prepare-grammar
booktx judge prepare-isolation
booktx judge status
booktx judge todo-next
booktx judge todo-status
booktx judge todo-resume
booktx judge insert
booktx judge lint-decisions
booktx judge audit-copies
booktx profile
booktx profile compare
booktx profile create
booktx profile list
booktx profile show
booktx qa-scan
booktx review
booktx review configure
booktx review status
booktx series
booktx series prepare
booktx series recipe
booktx series recipe write
booktx source
booktx source analysis
booktx source analyze
booktx source ignore-candidate
booktx source interview-answer
booktx source interview-next
booktx source interview-plan
booktx source interview-skip
booktx source interview-status
booktx source review-candidate
booktx source status
booktx status
booktx validate
booktx version
booktx version current
booktx version list
booktx version show
booktx whoami
booktx translate
booktx termbase
booktx pass-through
```

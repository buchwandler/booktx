---
name: booktx
description: Operate booktx safely through its human-first lifecycle and profile-local agent protocol.
---

# booktx skill

Use the live CLI help as the command authority. Use this skill for booktx
translation projects, not for changing the booktx implementation itself.

## Mandatory startup dispatch

Before any other filesystem inspection or booktx command:

1. Read a local `AGENTS.md` completely when it exists.
2. Run `booktx mode . --json` and dispatch by its reported protocol.
3. In a selection/judge profile, run `booktx judge status .` first and never run a `booktx translate` mutation command.
4. In profile-root mode, use only profile-local relative paths; do not inspect parents/absolute paths or chain commands with `;`, `&&`, `||`, or shell loops.
5. If the judge snapshot is missing or invalid, stop and report the project-root preparation command.

## Core contract

`booktx` prepares Markdown and EPUB documents. It extracts records, stores
profile-local translation data, validates submissions, and builds output. It
does not translate text and does not make network calls.

The source-first layout is:

```text
.booktx/                    shared source-derived state
translations/<profile>/     one profile's mutable translation state
```

`.booktx/` contains source configuration, manifests, protected names, chapter
maps, chunks, and source-analysis evidence. A profile contains
`.booktx-profile.json`, `config.toml`, `identity.json`, `context.json`,
`context.md`, canonical translation state, version ledgers, tasks, todos,
ingest, reviews, reports, and output.

New profiles currently default to the v2 canonical store. The shard-based v3
backend is opt-in and uses this layout when activated:

```text
translation-store/
  manifest.json
  current/<chunk>.json
  translation-candidates/<chunk>.json
  review-candidates/<chunk>.json
  transactions/
```

Generated translated exports, indexes, reports, and output are derived files.
Never edit canonical store shards or generated files directly.

## Runtime modes

From the project root, pass `--profile PROFILE` to every command that needs
profile data:

```bash
booktx guide ./book --profile PROFILE
booktx context status ./book --profile PROFILE
booktx translate next ./book --profile PROFILE --unit batch --max-words 800 --format block
```

For isolated work, start inside `translations/PROFILE/`:

```bash
booktx mode .
booktx doctor isolation .
booktx source status .
booktx context status .
booktx translate next . --unit batch --max-words 800 --format block
booktx translate lint-block . --task-id TASK --file ingest/TASK.block.txt --format block
booktx translate insert . --task-id TASK --file ingest/TASK.block.txt --format block
booktx validate .
booktx build .
```

The `.booktx-profile.json` marker binds the profile root to its enclosing
project, profile configuration, target locale, and extracted source identity.
This is booktx-mediated isolation, not an operating-system sandbox. Do not use
parent paths, absolute paths, sibling profile paths, shell globs, or arbitrary
filesystem inspection in isolated mode. If a command reveals a sibling or
parent path, stop and report an isolation defect.

There is no project-wide profile selector and target-state commands do not
infer one profile from the profiles present. Create, list, compare, or migrate
profiles from project-root mode.

## Human-first lifecycle

The human workflow surfaces are `guide`, `glossary`, `identity`, `context`,
`source`, `profile`, `series`, `review`, and `judge`:

```bash
booktx init ./book --source-file ./book.epub --source-lang en
booktx extract ./book
booktx chapters ./book --audit
booktx profile create ./book PROFILE --target de --target-locale de-DE --model MODEL
booktx guide ./book --profile PROFILE
```

Prepare context and source policy:

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx source analyze ./book --write --sync-profiles
booktx source interview-plan ./book --profile PROFILE --write
booktx source interview-next ./book --profile PROFILE --format markdown
booktx context questionnaire ./book --profile PROFILE --stdout
```

Recommendations and questionnaire output are not approval. Stop and show them
to the user. Do not run `context approve` or `context mark-ready` until the
user has explicitly approved or edited the answers:

```bash
booktx context approve ./book --profile PROFILE Q001 \
  --text "<USER_APPROVED_TEXT>" --approved-by "user:NAME"
booktx context mark-ready ./book --profile PROFILE
```

Use the canonical human terminology surface:

```bash
booktx glossary status ./book --profile PROFILE
booktx glossary mandate ./book "Empire" --profile PROFILE \
  --target "Imperium" --forbid "Reich"
booktx glossary reset ./book "Empire" --profile PROFILE \
  --target "Imperium" --require-target
booktx identity set ./book --profile PROFILE \
  --actor user:NAME --harness codex --model MODEL
```

`context *-term` commands and `termbase` are advanced compatibility or storage
surfaces. Route ordinary terminology corrections through `booktx glossary`.

## Agent task protocol

Before an agent starts, prepare the matching generated instructions:

```bash
booktx agents write ./book --mode isolated --profile PROFILE
```

From the profile root, read `context.md` and confirm that `context.json` is
ready. Request one bounded task:

```bash
booktx translate next . --unit batch --max-words 800 --format block
```

This writes `tasks/TASK.agent.md`, `tasks/TASK.source.block.txt`,
`ingest/TASK.block.txt`, and `ingest/TASK.json`.

Read `tasks/TASK.agent.md` first. Edit only the generated
`ingest/TASK.block.txt`. Keep record headers, placeholder tokens, protected
names, and required inline markup unchanged. Treat `# glossary:`, `# style:`,
and `# termbase:` as source-only directives and never copy them into target
text. Lint before the first insert:

```bash
booktx translate lint-block . \
  --task-id TASK --file ingest/TASK.block.txt --format block
```

Submit only after lint passes:

```bash
booktx translate insert . \
  --task-id TASK --file ingest/TASK.block.txt --format block
```

For a translation todo, a task is one bounded batch inside the user's todo.
After every successful insert, run the printed scoped check and query the exact
todo status. If the result says `must_continue=true`, resume the same todo in
this assistant turn. A successful insert is not a stop condition, and the
agent must not ask the user to say `continue`. Stop only when the todo is
complete, booktx reports a blocker, the user explicitly stops the run, or a
documented harness limit is reached.

The task records the profile, target language and locale, translation version,
source hash, profile hashes, and an immutable effective context view under
`context-history/views/<sha>/`.

For multi-chapter work, use a bounded todo:

```bash
booktx translate todo-next . --chapters 3 --batch-words 800 --write --resume --format block
```

Do not bypass a failed todo with a large unbounded task. Report the error and
stop. Use `booktx check . --chapter CHAPTER --fail-on-warnings` between batches
and `booktx validate . --fail-on-warnings` before the final build.

After translation or review changes, indexes may be regenerated:

```bash
booktx translate export-index .
```

## Context and provenance guardrails

- Do not translate from context that has not passed the human approval gate.
- Treat `context.json` as authoritative and `context.md` as rendered output.
- Write chapter notes with `booktx context chapter-note`, not by editing
  `context.md`.
- Use `booktx context sync` for sibling profiles in one book.
- Use `context export-pack` and `context import-pack` for policy transfer between
  books. Review conflicts before `--write`.
- Each task uses an immutable context-view snapshot. Do not mix files between
  profiles.
- A chapter-note append affects the next task context but does not create a
  dotted version by itself.

Source analysis is a review queue, not glossary approval:

```bash
booktx source analyze ./book --write --sync-profiles
booktx source analysis ./book/translations/PROFILE
booktx context prefill ./book --profile PROFILE --from-source-analysis
booktx context promote-candidate ./book CAND-... --profile PROFILE \
  --target "TARGET" --require-target --enforce error --write
```

## Quality and judge workflows

Quality review is separate from initial translation:

```bash
booktx review configure ./book --profile PROFILE
booktx review status ./book --profile PROFILE
booktx review next . --pass 1
booktx review insert . --review-task-id TASK --file reviews/TASK.block.txt
```

For cross-profile comparison or revision, prepare the judge profile from the
project root. Use `--purpose revise` for a single-source revision profile and
require an explicit `copy` or `edited` decision for every record:

```bash
booktx judge create-profile ./book JUDGE \
  --target de --sources PROFILE_A,PROFILE_B --model MODEL
booktx judge prepare-isolation ./book --profile JUDGE --write
booktx judge prepare-grammar ./book --source-profile PROFILE_A \
  --profile JUDGE_GRAMMAR --model MODEL --write
```

Use `booktx judge todo-next`, `todo-status`, and `todo-resume` for every
multi-batch judge request. A judge task is one bounded batch; the judge todo is
the user's requested chapter scope. One-command-at-a-time is a safety rule,
not a one-batch-per-turn limit.

For a new scope, create the todo with an explicit chapter range and bounded
batch policy. After every insert, query authoritative todo status and resume the
same todo until it is complete in the same assistant turn:

```text
1. judge todo-status --latest --json
2. if incomplete, judge todo-resume --latest
3. read, edit, lint, and insert exactly one generated task
4. repeat from step 1; a successful insert is not a stop condition
```

Stop only for todo completion, invalid context/snapshot/source state, a
documented blocker, an explicit user stop, or an unavoidable harness limit. On
a harness limit, report the exact persisted counts and resume command; do not
claim completion from attempted batches. Use only explicit `copy`/`edited`
decisions and never run `booktx translate` mutators in a selection profile. If a
profile is contaminated by direct translation writes or lacks judge provenance,
create a fresh profile; do not synthesize decisions from the contaminated output.

## Markdown and EPUB rules

Markdown extraction preserves front matter, code, URLs, raw HTML, and internal
placeholders. Translate visible prose only. Preserve `__TAG_NNN__` and
`__NAME_NNN__` tokens exactly.

EPUB records may contain constrained inline XHTML. Translate text nodes only.
Preserve the source tag sequence, attributes, opaque elements, and placeholder
semantics. Do not add block tags, scripts, styles, comments, processing
instructions, event handlers, new attributes, or new inline elements. The same
build-grade preflight is used by insertion, validation, check, and build.

Pass-through profiles are generated reconstruction checks:

```bash
booktx profile create-pass-through ./book passthrough_en
booktx validate ./book --profile passthrough_en
booktx build ./book --profile passthrough_en
```

## Migration and legacy paths

Migrate a legacy single-layout project from project root:

```bash
booktx profile migrate-current ./book PROFILE
```

After migration, old `.booktx/` translation files are legacy input only. New
mutable files belong under `translations/<profile>/`. Maintenance commands such
as `translate import-legacy`, `translate migrate-store`, and
`translate migrate-inline-xhtml` are for migration only.

## Safety rules

- Do not claim that a command exists without checking `booktx --help`.
- Do not use removed top-level `translation`, `model`, `actor`, or `harness`
  namespaces. Use `translate`, `identity set`, and the human surfaces above.
- Do not edit `.booktx/chunks/`, a profile store, tasks, or generated exports
  directly during normal work.
- Do not skip user approval or mark context ready from agent judgment.
- Do not mix profile or judge files.
- Record and report validation warnings; do not hide existing failures.

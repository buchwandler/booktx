# Commands

## Source-first setup

```bash
booktx init ./book --source-file book.epub --source-lang en
booktx extract ./book
```

Legacy one-step initialization still works:

```bash
booktx init ./book --target de --source-file book.epub --source-lang en
```

That creates and selects a default profile such as `de_default`.

## Profile commands

```bash
booktx profile create ./book PROFILE --target de --target-locale de-DE --model codex-openai/gpt-5.5@low
booktx profile list ./book
booktx profile show ./book PROFILE
booktx profile compare ./book --profiles PROFILE,PROFILE_B --record 0001-000001
booktx profile migrate-current ./book PROFILE
booktx profile create-pass-through ./book passthrough_en
```

## Generated AGENTS.md files

Before starting an agent harness, write the matching harness instructions:

```bash
booktx agents write . --mode isolated --profile PROFILE
cd translations/PROFILE
```

For project-root collaboration:

```bash
booktx agents write . --mode collaborative
```

`booktx agents status .` reports which `AGENTS.md` files are present and whether
they are stale, and `booktx agents clean . --mode all` removes only the files
booktx generated. booktx deletes only `AGENTS.md` files it generated itself;
user-authored files are never silently overwritten or removed. In isolated
profile-root mode, `agents status`/`clean`/errors expose only the local
`AGENTS.md` and never print parent paths, `../`, or sibling profile names.

## Context commands

All context files are profile-local:

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx context questions ./book --profile PROFILE
booktx context recommend ./book --profile PROFILE Q001 --text de-DE --reason "profile target locale"
booktx context questionnaire ./book --profile PROFILE --stdout
# Stop for user approval, then record the approved answer.
booktx context approve ./book --profile PROFILE Q001 --text de-DE --approved-by "user:<USER>"
booktx context mark-ready ./book --profile PROFILE
booktx context render ./book --profile PROFILE --write
booktx context chapter-note ./book --profile PROFILE 0010 --decision "Keep title literal"

# Same-book policy sync across sibling profiles (dry run by default).
booktx context sync ./book \
  --from PROFILE \
  --all-compatible \
  --section glossary \
  --term "Empire"
```

`context export-pack` / `import-pack` move reusable policy between different
books. `context sync` reuses the same merge semantics for sibling profiles
inside one book project and is rejected in isolated profile-root mode.

## Series setup commands

Use `booktx series prepare` for the normal "start the next book in a series"
workflow:

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

`booktx series prepare` is dry-run by default. `--write` performs the setup,
writes `.booktx/reports/series-prepare.json` and `.md`, and stops before
translation and before automatic `context mark-ready`.

Review handoff:

```bash
booktx context questionnaire ./book5 --profile de_glm_5_2 --stdout
booktx context status ./book5 --profile de_glm_5_2
booktx context render ./book5 --profile de_glm_5_2 --write
booktx context mark-ready ./book5 --profile de_glm_5_2
booktx agents write ./book5 --mode isolated --profile de_glm_5_2
```

Pack mode replaces `--from-book`:

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

Write a reusable recipe once, then reuse it for the next book:

```bash
booktx series recipe write ./book5 \
  --profile de_glm_5_2 \
  --series-id shadows-of-the-apt \
  --title "Shadows of the Apt German series context" \
  --output ../shadows-of-the-apt.de.booktx-series.toml

booktx series prepare ./book6 \
  --source-file ./book6/book6.epub \
  --from-book ./book5 \
  --recipe ../shadows-of-the-apt.de.booktx-series.toml \
  --write
```

## Chapter detection and audit

```bash
booktx chapters ./book                       # detect, persist, and list chapter ranges
booktx chapters ./book --audit               # audit EPUB TOC vs. extracted spans and map
booktx chapters ./book --audit --json        # machine-readable audit output
```

`booktx chapters` refreshes `.booktx/chapter-map.json` and lists each chapter's
chunk and record range. `--audit` is EPUB-only and read-only: it compares the
visible contents page against extracted spans, navigation, and the chapter
map, then writes `.booktx/reports/chapter-audit.json`. EPUB `booktx extract`
already generates both files and prints a warning when findings exist; run
`--audit` for details. `booktx status` recomputes the audit summary, and new
work selection blocks only on `error` findings (warning-only findings stay
non-blocking).

## Status and identity

```bash
booktx status ./book
booktx status ./book --profile PROFILE
booktx whoami ./book --profile PROFILE
booktx actor whoami ./book --profile PROFILE
booktx harness whoami ./book --profile PROFILE
booktx model whoami ./book --profile PROFILE
```

When multiple profiles exist and none is active, target-dependent commands fail
until you pass `--profile` or select one.

## Translation workflow

```bash
booktx translate next ./book --profile PROFILE --unit batch --max-words 800 --format block
booktx translate insert ./book --profile PROFILE --task-id TASK --file translations/PROFILE/ingest/TASK.block.txt --format block
booktx translate task-status ./book --profile PROFILE --task-id TASK
booktx translate set-record ./book --profile PROFILE --task-id TASK --record-id RECORD_ID --stdin
booktx translation get-record ./book --profile PROFILE 74@38 --before 2 --after 2
booktx translation list ./book --profile PROFILE --chapter 10
booktx translation compare ./book --profile PROFILE 74@38 --versions 1.1,1.2
booktx translation activate ./book --profile PROFILE 74@38 1.2
booktx translation review ./book --profile PROFILE 74@38 --activate 1.2 --note "Better rhythm"
booktx translation revise-record ./book --profile PROFILE 74@38 --target "Revised target text"
booktx translation revise-block ./book --profile PROFILE --file ingest/fixes.block.txt --format block --activate
booktx translate export ./book --profile PROFILE
booktx translate export-index ./book --profile PROFILE
booktx translate export-index ./book --profile PROFILE --kind source
booktx translate export-index ./book --profile PROFILE --kind target
booktx translate export-index ./book --profile PROFILE --kind source-target
booktx translate export-index ./book --profile PROFILE --json
booktx translate export-index ./book --profile PROFILE --fail-on-warn
booktx translate search ./book --profile PROFILE --target "Wespen" --before 1 --after 1
booktx translate search ./book --profile PROFILE --source "empire" --jsonl
booktx translate migrate-inline-xhtml ./book --profile PROFILE  # normalize inline XHTML in stored targets
booktx source record ./book --profile PROFILE 74@38            # inspect one source record
booktx source chapter ./book --profile PROFILE 0001            # inspect one source chapter
booktx source analyze ./book                                     # dry-run translation-risk review queue
booktx source analyze ./book --write                             # write canonical JSON + Markdown review queue
booktx source analyze ./book --write --sync-profiles             # also refresh profile snapshots
booktx source analysis ./book/translations/PROFILE             # read the current profile snapshot
booktx context prefill ./book --profile PROFILE --from-source-analysis
booktx context prefill ./book --profile PROFILE --from-source-analysis --include-advisory --write
booktx context prefill ./book --profile PROFILE --from-source-analysis --consolidate-imported-policy --write
booktx context promote-candidate ./book CAND-... --profile PROFILE --as-question --write
booktx source ignore-candidate ./book CAND-... --reason "ordinary vocabulary" --write
booktx source review-candidate ./book CAND-... --reason "checked, no glossary decision needed" --write
booktx source interview-plan ./book --profile PROFILE --write       # write generated interview ledger
booktx source interview-status ./book --profile PROFILE --fail-if-open
booktx source interview-next ./book --profile PROFILE --format markdown
booktx source interview-answer ./book CAND-... --profile PROFILE --target TARGET --write
booktx source interview-skip ./book CAND-... --profile PROFILE --disposition ignored --reason "REASON" --write
```

`source analyze` is project-root only and does not write unless `--write` is
provided. Its JSON report is authoritative; Markdown is a generated review
queue. The default output is a translation-risk review surface, not a bulk
glossary prefill. Profile-root mode can only read its own generated snapshot
with `source analysis`; if the snapshot is missing, rerun project-root
analysis with `--write --sync-profiles`.

`context prefill --from-source-analysis --gate-readiness` creates required questions for readiness-gated workflows.

`context prefill --from-source-analysis` is dry-run by default and now creates
review questions for binding/name/rare candidates instead of open glossary
entries. Advisory glossary entries stay opt-in behind `--include-advisory`.

`translate export` writes store-backed accepted translations as legacy-compatible chunk files under `translated/`.

`translate export-index` writes three generated editor QA indexes under `translations/<profile>/`: `source-index.json` (source text only), `target-index.json` (target text only), and `source-target-index.json` (slim side-by-side view). Use `--kind source`, `--kind target`, or `--kind source-target` (repeatable) to write only selected kinds. `--fail-on-warn` blocks target-based indexes on warnings. `--json` prints the summary as JSON. All three files are generated artifacts safe to delete and regenerate. They never contain canonical state and must not be used as build input.

Profile-root mode works without `--profile`:

```bash
cd translations/de_default
booktx translate export-index .
rg "Wespen" target-index.json
nvim source-target-index.json
```

## Bounded agent runs

```bash
booktx translate todo-next ./book --profile PROFILE --chapters 3 --batch-words 800 --write
booktx translate todo-next ./book --profile PROFILE --chapters 3 --batch-words 800 --max-run-words 12000 --write --json
booktx translate todo-status ./book --profile PROFILE --latest
booktx translate todo-status ./book --profile PROFILE --todo-id TODO --json
booktx translate todo-resume ./book --profile PROFILE --latest --format block
booktx translate todo-resume ./book --profile PROFILE --todo-id TODO --format block
booktx translate todo-next ./book --profile PROFILE --chapters 5 --batch-words 800 --skip-current --write
booktx translate todo-next ./book --profile PROFILE --chapters 3 --start-chapter 0017 --batch-words 800 --write
```

Creates a durable todo under `translations/<profile>/todos/` that describes the
bounded run: chapters to complete, per-task word budget, advisory run budget,
and stop conditions. The agent reads the todo markdown and follows
`todo-status -> todo-resume -> insert -> check --chapter CHAPTER` until
complete or a stop condition fires. Use `booktx validate --fail-on-warnings`
for the final pre-build check only. This is NOT a translation submission; the
agent still fills ingest files and runs `translate insert` for each batch.
`--max-run-words` is advisory only.

## Version commands

Versions are profile-local:

```bash
booktx version current ./book --profile PROFILE
booktx version list ./book --profile PROFILE
booktx version show ./book --profile PROFILE 1.2
booktx version select ./book --profile PROFILE 1.2
booktx version set-label ./book --profile PROFILE 1 "GPT 5.5"
booktx version fork-context ./book --profile PROFILE --note "Manual context split"
```

`version list` now reports baseline-scoped subversions. Routine chapter-note
appends keep the same dotted version; baseline policy changes create or select
the next subversion. `translate next` task output also includes baseline and
context-view metadata for the immutable task snapshot it created.

## Validate and build

```bash
booktx validate ./book --profile PROFILE
booktx validate ./book --profile PROFILE --fail-on-warnings
booktx validate ./book --profile PROFILE --chapter 0005
booktx validate ./book --profile PROFILE --task-id TASK_ID
booktx validate ./book --profile PROFILE --json
booktx build ./book --profile PROFILE
booktx build ./book --profile PROFILE --require-complete
```

`--chapter` and `--task-id` scope validation to a specific chapter or task.
Use `--json` for machine-readable output.

`--fail-on-warnings` keeps default validate behavior unchanged unless you opt
into warning-fatal automation.

## QA scan and EPUB inspection

```bash
booktx qa-scan ./book --profile PROFILE            # targeted QA scan of translated targets
booktx epub inspect ./book --profile PROFILE          # inspect built EPUB XHTML output
booktx epub inspect ./book --profile PROFILE --chapter 0001 --contains "Wespen"
booktx epub grep ./book --profile PROFILE "Wespen"   # grep built EPUB XHTML for text
booktx epub extract-text ./book --profile PROFILE     # extract plain text from built EPUB XHTML
```

`qa-scan` runs targeted quality checks (glossary/forbidden-term/regex) over
effective translated targets without a full validate run. The `epub`
commands read the profile-local `output/` directory produced by `booktx build`; run `booktx build .` first if `no EPUB output directory` is reported.

## Translation preference dictionary / termbase

```bash
booktx termbase status ./book --profile PROFILE --json
booktx termbase add --scope global --language de --id LEX-MOULDY --source "mouldy principles" --preferred "schäbige Prinzipien" --forbid "schimmligen Prinzipien" --approve
booktx termbase scan-source ./book --profile PROFILE --jsonl
booktx termbase audit ./book --profile PROFILE --jsonl
booktx termbase write-review ./book --profile PROFILE --pass 1
booktx termbase promote-context ./book --profile PROFILE --entry LEX-MOULDY --as-question
booktx termbase export --scope global --language de --output ./termbase-de.json
booktx termbase import --scope global --language de --input ./termbase-de.json --mode merge
```

Use the termbase for recurring word-sense preferences, literalism traps, and
cross-book lexical habits that should only appear in prompts when the source
cue actually matches the selected records. Do **not** use it as a replacement
for the glossary: names, invented terms, and mandatory enforced terminology
still belong in `booktx context ...`.

Resolution order is deterministic:

```text
global base < global locale < project base < project locale < profile base < profile locale
```

Higher-precedence entries with the same id replace lower entries wholesale, and
`status=disabled` acts as a tombstone. Only approved effective entries
participate in source matching, task prompts, or audits.

In isolated profile-root mode, read-only termbase commands and profile-scope
writes work without `--profile`, but global/project mutations remain blocked.
Global path output is redacted to `~` or `$BOOKTX_TERMBASE_DIR/...`.

## `check` -- scoped build-preflight validation

```bash
booktx check ./book --profile PROFILE --chapter 0005 --fail-on-warnings
booktx check ./book --profile PROFILE --task-id TASK_ID --json
```

`check` is a human-friendly alias for scoped validation + EPUB inline-XHTML
preflight. It defaults to `--fail-on-warnings`. Use it after each chapter
translation and before build.

Outputs land under:

```text
translations/<profile>/reports/
translations/<profile>/output/
```

`check --epub-output` audits the existing expected EPUB output path against the
resolved EPUB output policy **without building or modifying it**. It errors
clearly when no output exists and emits the same findings in text and JSON
modes. Use it after a build to confirm the output's language contract and
review reported CSS cascade conflicts:

```bash
booktx check ./book --profile PROFILE --epub-output --json
```

## Pass-through validation

`booktx pass-through` generates source-as-target translated chunks from the
extracted source chunks, validates complete coverage, and rebuilds the output.
It is a reconstruction fixture, not a translation:

```bash
booktx pass-through ./book --profile passthrough_en --create
booktx pass-through ./book --profile passthrough_en --no-build
```

`--profile` is always required. Use `--clear-store` only when reusing a
pass-through profile that has stray store records. Compare the rebuilt output
against the source with an EPUB diff viewer.

## JSON output for machine consumers

Most read commands accept `--json`. Examples:

```bash
booktx profile list ./book --json
booktx profile show ./book PROFILE --json
booktx whoami ./book --profile PROFILE --json
booktx status ./book --profile PROFILE --json
booktx version show ./book --profile PROFILE 1.2 --json
```

`profile list`/`profile show`/`whoami` report the live identity from
`translations/<profile>/identity.json`, so they stay consistent after
`booktx model set`, `actor set`, or `harness set`.

## Context question lifecycle

Questions start as `open`. Agents may store draft defaults with `context recommend`, which sets `recommended` but does not answer the question or change style policy. User-approved decisions are recorded with `context approve`, which stores `answer_source=user`, approval metadata, and applies style updates. Required dynamic questions can be added with `context add-question --required` after source review. Use `context questionnaire --stdout` to show a user-facing approval form. `context mark-ready --force --reason ...` is only for emergency or migration cases.

## Review commands (`booktx review`)

- `booktx review configure . --show` -- show current quality review config
- `booktx review configure . --enable --pass 1 --name "Flow review" --mode manual --enforce warn` -- enable review with one pass (see `docs/profiles.md` for all flags)
- `booktx review configure . --disable` -- disable quality review entirely
- `booktx review status .` -- report review coverage by pass (eligible/reviewed/missing/stale/blocked); JSON includes `next_command`, `first_missing_record`, `first_missing_chapter`
- `booktx review next . --pass 1` -- create the next durable review task for a pass; supports `--selection missing|stale|reviewed|all|changed-base` and `--base active_translation|active_review|pass:N`
- `booktx review next . --pass 1ion reviewed --base active_review` -- rerun a pass over already-reviewed records, creating `R1.2` from `R1.1`
- `booktx review insert . --review-task-id TASK --file reviews/TASK.block.txt --format block` -- parse and accept a review submission
- `booktx review activate . RECORD R1.2` -- manually activate an existing review candidate for a record
- `booktx review deactivate . RECORD` -- deactivate the active review, falling back to the active translation version
- `booktx review revise-record . RECORD --base-review R1.2 --stdin` -- revise an accepted review candidate by creating a new same-pass rerun
- `booktx review todo-next . --passes 1 --chapters 2 --batch-words 900 --write` -- create a bounded multi-pass review todo over chapters with review gaps (profile-local `review-todos/`)
- `booktx review todo-status . --review-todo-id TODO` -- report progress for a durable review todo (remaining chapters/passes)
- `booktx review todo-resume . --review-todo-id TODO --format block` -- emit the next review block for a durable review todo

Enable quality review via CLI (preferred) or TOML:

```bash
booktx review configure . --enable --pass 1 --name "Flow review" --mode manual --enforce warn
```

## Judge commands (`booktx judge`)

Use project-root mode to create or refresh a judge source snapshot. After
`booktx judge sync-sources` or `booktx judge prepare-isolation`, a selection
profile may run `booktx judge status/next/record/insert` from its profile root
without sibling profile access.

`--purpose compare` (the default) builds a multi-source selection profile.
`--purpose revise` builds a single-source revision profile that requires an
explicit copy or edited judge decision for every record; the deterministic
commands `accept-identical`, `sweep-identical`, and `prefill-policy-fixes` are
disabled there. See _Single-source judge revision profiles_ in
`docs/profiles.md`.

`judge create-profile` also accepts:

```text
--revision-focus general|grammar
```

Use `general` for the existing broad revise contract. Use `grammar` when you
want an isolated single-source grammar-fix run that freezes established
wording, terminology, names, tone, and register while still requiring an
explicit decision for every record.

```bash
booktx judge create-profile ./book JUDGE_PROFILE \
  --target de \
  --target-locale de-DE \
  --sources PROFILE,PROFILE_B \
  --context-from PROFILE \
  --model gpt-5.5 \
  --purpose compare

booktx judge create-profile ./book JUDGE_GRAMMAR \
  --target de \
  --target-locale de-DE \
  --sources de_glm_5_2 \
  --context-from de_glm_5_2 \
  --model gpt-5.6 \
  --purpose revise \
  --revision-focus grammar

booktx judge status ./book --profile JUDGE_PROFILE --sources PROFILE,PROFILE_B

booktx judge accept-identical ./book \
  --profile JUDGE_PROFILE \
  --sources PROFILE,PROFILE_B \
  --unit chapter \
  --chapter 0001 \
  --max-records 100 \
  --write

booktx judge next ./book \
  --profile JUDGE_PROFILE \
  --sources PROFILE,PROFILE_B \
  --unit chapter \
  --chapter 0001 \
  --max-records 8 \
  --format decisions

booktx judge record ./book \
  --profile JUDGE_PROFILE \
  --sources PROFILE,PROFILE_B \
  --record 0001-000001 \
  --format decisions

booktx judge insert ./book \
  --profile JUDGE_PROFILE \
  --judge-task-id TASK \
  --file translations/JUDGE_PROFILE/judge-ingest/TASK.decisions.txt \
  --format decisions

booktx judge reset-ingest ./book \
  --profile JUDGE_PROFILE \
  --judge-task-id TASK \
  --format decisions \
  --write
```

### Prepare isolation (project-root)

```bash
booktx judge sync-sources ./book --profile JUDGE_PROFILE --write
booktx judge prepare-isolation ./book --profile JUDGE_PROFILE --write
```

`sync-sources` copies source candidate stores into an immutable profile-local snapshot. `prepare-isolation` syncs and writes judge-specific `AGENTS.md`. Both are dry-run by default; pass `--write` to publish.

### Isolated judge workflow (profile root)

```bash
cd translations/JUDGE_PROFILE
booktx judge status .
booktx judge accept-identical . --unit chapter --chapter 0001 --max-records 100 --write
booktx judge next . --unit chapter --chapter 0001 --max-records 8 --format decisions
booktx judge insert . --judge-task-id TASK --file judge-ingest/TASK.decisions.txt --format decisions
booktx judge reset-ingest . --judge-task-id TASK --format decisions --write
booktx judge continue . --max-records 8
```

For `decision_kind: copy`, leave `TARGET` empty; booktx copies the selected
candidate exactly. Only `edited` decisions require a non-empty `TARGET`.

For revise profiles, do **not** use `accept-identical`; it is valid only in
compare mode. In `--revision-focus grammar`, `booktx judge status .` reports the
revision focus plus copy/edited counts and edit rate so model runs can be
compared without adding a separate reporting command.

## Glossary repair and chapter note reset

```bash
# Replace forbidden targets (full replacement, not append).
booktx context add-term . "empire" --target "Imperium" --forbid "Reich" --forbid "Empire"

# Append forbidden targets explicitly.
booktx context add-term . "empire" --append-forbid "Kaiserreich"

# Clear all forbidden targets.
booktx context add-term . "empire" --clear-forbidden

# Remove a wrong glossary entry.
booktx context remove-term . "empire"
booktx context remove-term . "empire" --missing-ok

# Reset one entry atomically.
booktx context reset-term . "empire" \
  --target "Imperium" \
  --forbid "Reich" --forbid "Empire" \
  --category "concept" --enforce error

# Replace an entire chapter note.
booktx context chapter-note . 0006 \
  --replace-all \
  --title "TWO" \
  --source-summary "..." \
  --translation-summary "..." \
  --decision "Keep Apt" \
  --open-issue "Check title rendering"
```

## Series context packs

Context is normally profile-local. Series-wide consistency is achieved by
importing an explicit context pack, not by sharing profile state. A pack
carries only reusable policy (style, global rules, glossary entries, approved
reusable question answers); it never carries records, candidates, tasks,
todos, stores, ledgers, identity, chapter contexts, or source state.

```bash
# Export from an approved profile context (dry-run-safe; refuses overwrite
# without --force; requires ready unless --allow-not-ready).
booktx context export-pack ./book1 \
  --profile PROFILE \
  --series-id shadows-of-apt \
  --title "Shadows of the Apt / German policy" \
  --output ./shadows-of-apt.en-de.booktx-context-pack.json

# Import into another book's profile. Dry run by default; --write commits.
booktx context import-pack ./book2 \
  --profile PROFILE \
  --file ./shadows-of-apt.en-de.booktx-context-pack.json

booktx context import-pack ./book2 \
  --profile PROFILE \
  --file ./shadows-of-apt.en-de.booktx-context-pack.json \
  --write
```

Import never mutates profile config, source state, identity, stores,
ledgers, or tasks. When policy changes it clears readiness and regenerates
`context.md`; run `booktx context mark-ready` again after approval. Conflicts
are reported as findings and can be resolved with `--conflict fail|keep-local|replace`. A task created before a binding glossary import is
rejected by the existing stale-policy guard; create a fresh task to use the
imported policy. In profile-root isolated mode, pack input and output paths
must resolve inside the current profile root.

## Terminology search and correction blocks

`booktx translation search` supports `--match any` (default, compatibility) and `--match all` for requiring every populated positive group, plus `--source-regex`, `--target-regex`, `--exclude-source`, `--exclude-source-regex`, and `--write-block ingest/name.block.txt`. Generated correction blocks are editable target-only blocks suitable for `translation revise-block`; the companion `.sources.txt` file is reference-only.

In isolated profile-root mode, generated and submitted block paths must be profile-local relative paths. Absolute paths, `..` traversal, and escaping paths are rejected.

### Context pack termbase import

`booktx context import-pack` is dry-run by default. `--write` commits context changes. Pack termbase entries are not written unless `--write-termbase` is also supplied, for example:

```bash
booktx context import-pack BOOK --profile PROFILE_B --file series-context.de.json --write --write-termbase --termbase-scope project
```

Use `booktx termbase status BOOK --profile PROFILE_B --scope effective` after import. Existing tasks created before context or termbase changes can be stale; create fresh tasks after policy changes.

### Source-analysis candidate to termbase

Promote a reviewed source-analysis candidate without hand-editing JSON:

```bash
booktx termbase promote-candidate ./book CAND-... --profile PROFILE --scope profile --preferred "TARGET" --preferred-policy required --severity error --approve --write
```

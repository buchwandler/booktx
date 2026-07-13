[![PyPI - Version](https://img.shields.io/pypi/v/booktx)](https://pypi.org/project/booktx/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/booktx)
[![codecov](https://codecov.io/gh/buchwandler/booktx/graph/badge.svg?token=EFO4GQF52W)](https://codecov.io/gh/buchwandler/booktx)

# booktx

`booktx` is a deterministic local CLI for preparing **Markdown** and **EPUB**
documents for translation by a human or coding agent. It extracts stable
records, stores profile-local translation state, validates submissions, and
rebuilds output. It does not translate text or make network calls.

## Install

```bash
pip install -e .
python -m pip install -e ".[dev,docs]"  # development and documentation work
```

Python 3.10 and newer are supported.

## Core model

```text
.booktx/                    shared source-derived state
translations/<profile>/     mutable state for one translation effort
TranslationStoreV2          current profile-local record store
```

A profile is the hard isolation boundary. Project-root commands that need a
profile require `--profile PROFILE`. Commands started inside a validated
`translations/<profile>/` directory use `.` and the `.booktx-profile.json`
marker to resolve that profile. There is no project-wide profile selector.

## Project layout

```text
book/
  source/book.epub
  .booktx/
    source-config.toml
    source-manifest.json
    names.json
    chapter-map.json
    chunks/
  translations/PROFILE/
    .booktx-profile.json
    config.toml
    identity.json
    context.json
    context.md
    translation-store/
      manifest.json
      current/
      translation-candidates/
      review-candidates/
      transactions/
    translation-version-ledger.json
    tasks/
    ingest/
    todos/
    reviews/
    reports/
    output/
```

`.booktx/` contains source configuration, manifests, protected names, chapter
metadata, and extracted chunks. Translation records, context, tasks, reviews,
ledgers, reports, and output belong under the selected profile. The canonical
store is shard-based under `translation-store/`; do not edit shard files
directly.

## Quickstart

```bash
booktx init ./demo --source-file ./book.epub --source-lang en
booktx extract ./demo
booktx chapters ./demo --audit
booktx profile create ./demo PROFILE --target de --target-locale de-DE --model MODEL
booktx guide ./demo --profile PROFILE
booktx context init ./demo --profile PROFILE --non-interactive
booktx source analyze ./demo --write --sync-profiles
booktx context questionnaire ./demo --profile PROFILE --stdout
```

Stop for human approval of policy and answers. Then record approved decisions,
mark context ready, prepare the agent workspace, and run bounded translation
work:

```bash
booktx context approve ./demo --profile PROFILE Q001 \
  --text "<USER_APPROVED_TEXT>" --approved-by "user:<USER>"
booktx context mark-ready ./demo --profile PROFILE
booktx agents write ./demo --mode isolated --profile PROFILE
booktx status ./demo --profile PROFILE
booktx check ./demo --profile PROFILE
booktx build ./demo --profile PROFILE
```

Use `booktx guide PROJECT --profile PROFILE` whenever you need the next human
action.

## Project-root and profile-root modes

From the project root, use explicit profile selection:

```bash
booktx source status ./demo
booktx context status ./demo --profile PROFILE
booktx translate next ./demo --profile PROFILE --unit batch --max-words 800 --format block
```

For isolated work, start the harness in `translations/PROFILE/` and use only
profile-local commands:

```bash
booktx mode .
booktx doctor isolation .
booktx source status .
booktx context status .
booktx translate next . --unit batch --max-words 800 --format block
booktx translate insert . --task-id TASK --file ingest/TASK.block.txt --format block
booktx validate .
booktx build .
```

The profile root is validated against its marker, profile configuration,
project root, and extracted source identity. Profile-root isolation is
booktx-mediated, not an operating-system sandbox. A command that reveals a
parent or sibling profile is an isolation defect.

## Human workflow surfaces

- `booktx guide` shows the current lifecycle stage and next human action.
- `booktx glossary` is the normal human terminology surface.
- `booktx identity set` updates profile identity defaults.
- `booktx translate` is the durable coding-agent task namespace.
- `booktx review` and `booktx judge` provide optional quality workflows.

For terminology decisions:

```bash
booktx glossary mandate ./demo "Empire" --profile PROFILE \
  --target "Imperium" --forbid "Reich"
booktx glossary status ./demo --profile PROFILE
```

For profile identity:

```bash
booktx identity set ./demo --profile PROFILE \
  --actor user:NAME --harness codex --model MODEL
```

## Bounded translation runs

```bash
booktx translate todo-next ./demo --profile PROFILE \
  --chapters 3 --batch-words 800 --write
booktx translate todo-status ./demo --profile PROFILE --latest
booktx translate todo-resume ./demo --profile PROFILE --latest --format block
```

Tasks snapshot the effective context view under
`translations/<profile>/context-history/views/<sha>/`. Keep record headers and
placeholder tokens unchanged in submissions. Do not edit the store directly.

## Formats and output

Markdown extraction preserves YAML front matter, code, URLs, raw HTML, and
inline token placeholders. Visible prose in paragraphs, headings, list items,
blockquotes, and table cells becomes translation records.

EPUB extraction stores constrained inline-XHTML records. Changed targets must
preserve the source inline skeleton and may change only human-readable text
nodes. Validation and build preflight reject new attributes, block elements,
opaque-element changes, and mismatched inline structure. EPUB output policy
updates target language metadata and may inject deterministic hyphenation CSS;
reader rendering remains outside booktx's control.

Pass-through profiles are generated reconstruction checks. Use a profile with
`kind = pass-through` and compare its generated output with an EPUB diff tool;
do not treat a generated fixture as a general byte-identity promise.

```bash
booktx profile create-pass-through ./demo passthrough_en
booktx validate ./demo --profile passthrough_en
booktx build ./demo --profile passthrough_en
```

## Profiles and migration

Create separate profiles for different languages, model experiments, or
context decisions:

```bash
booktx profile create ./demo PROFILE_A --target de --model MODEL
booktx profile create ./demo PROFILE_B --target de --model OTHER_MODEL
booktx profile compare ./demo --profiles PROFILE_A,PROFILE_B --record 0001-000001
```

Cross-profile operations are project-root operations. To migrate a legacy
single-layout project, use:

```bash
booktx profile migrate-current ./demo PROFILE
```

Legacy `.booktx/` translation paths are migration input only. New profile
projects keep mutable state under `translations/<profile>/`.

## Series and quality workflows

Prepare a subsequent book with the human review stop intact:

```bash
booktx series prepare ./book5 --source-file ./book5.epub \
  --from-book ./book4 --profile PROFILE --series-id series-id \
  --title "Series policy" --target de --target-locale de-DE --model MODEL --write
```

Review the generated questionnaire before running `context mark-ready`.
Quality review is configured and monitored with `booktx review configure` and
`booktx review status`. Comparison or revision profiles are prepared with
`booktx judge create-profile`, `booktx judge prepare-isolation`, and
`booktx judge prepare-grammar`.

## Development and documentation

```bash
python -m pytest -q
python -m pytest tests/test_docs_consistency.py -q
sphinx-build -W -b html docs docs/_build/check
make -C docs check
bash -n docs/build.sh
ruff check .
git diff --check
python -m mypy booktx
```

The mypy command is a status check. Its result must be reported accurately;
this project does not claim a clean type check unless the command exits zero.

See the [documentation index](docs/index.md), [commands](docs/commands.md),
[quickstart](docs/quickstart.md), [profiles](docs/profiles.md),
[context](docs/context.md), [agent workflow](docs/agent-workflow.md),
[format guides](docs/markdown.md), [EPUB guide](docs/epub.md),
[translation contract](docs/translation-contract.md),
[troubleshooting](docs/troubleshooting.md), and [API reference](docs/api.md).

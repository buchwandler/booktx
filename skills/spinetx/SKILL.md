---
name: spinetx
description: Use this skill when working with spinetx projects
---

# spinetx Skill

## Primary goal

Work safely with `spinetx`, a deterministic local CLI that prepares Markdown and EPUB documents for translation. `spinetx` extracts source text into JSON chunks, a coding agent or human fills translated JSON, then `spinetx validate` checks the contract and `spinetx build` reconstructs the output document.

Do not translate outside the JSON contract. Do not alter source files unless the user explicitly asks for package maintenance.

## When to use this skill

Use this skill for any of these tasks:

- Translate `.spinetx/chunks/NNNN.json` into `.spinetx/translated/NNNN.json`.
- Inspect, validate, or repair translated chunk files.
- Run `spinetx extract`, `spinetx next`, `spinetx validate`, or `spinetx build`.
- Maintain the `spinetx` Python package, especially extraction, placeholders, validation, rebuild, or CLI behavior.
- Review EPUB/Markdown translation safety and placeholder preservation.

## Core contract

A source chunk looks like this:

```json
{
  "chunk_id": "0001",
  "source_language": "en",
  "target_language": "de",
  "records": [
    {
      "id": "0001-000001",
      "source": "__NAME_001__ looked at __NAME_002__.",
      "protected_terms": ["Alice", "Mr. Smith"],
      "placeholders": [
        { "token": "__NAME_001__", "original": "Alice", "kind": "name" },
        { "token": "__NAME_002__", "original": "Mr. Smith", "kind": "name" }
      ]
    }
  ]
}
```

The translated file must be written to `.spinetx/translated/0001.json` and must look like this:

```json
{
  "chunk_id": "0001",
  "records": [
    {
      "id": "0001-000001",
      "target": "__NAME_001__ sah __NAME_002__ an."
    }
  ]
}
```

## Non-negotiable translation rules

- Return or write only a JSON object for translated chunks. No Markdown fences. No comments. No explanatory prose.
- Keep `chunk_id` exactly unchanged.
- Keep every record `id` exactly unchanged.
- Keep the same number and order of records unless the user is explicitly asking to repair source chunks. For normal translation, never merge, split, add, or delete records.
- Translate only the `source` text into `target` text.
- Preserve every `__NAME_NNN__` and `__TAG_NNN__` token exactly. Same spelling, same underscores, same digits.
- Do not invent new placeholder tokens.
- Do not replace a `__NAME_NNN__` token with the visible original name. Build restores names later.
- Do not translate inline code, URLs, tag fragments, or protected names hidden behind placeholders.
- Keep each `target` non-empty.

## Required context gate

Before translating any chunk or chapter, read `.spinetx/context.md`. If it does not exist, or `.spinetx/context.json` has `ready: false`, do not translate. Ask the user the context questionnaire first and write the answers to `.spinetx/context.json`, then render `.spinetx/context.md`.

Glossary entries in the context override ordinary dictionary translations. Do not use a target listed under `forbidden_targets`. For this book, do not translate `Lowlands` / `Lowlander` as `Niederlande` / `Niederländer` unless the user explicitly approves it in context.

Required sequence:

1. Run or ask for context building before translation.
2. Read `.spinetx/context.md` before opening any chunk.
3. If `.spinetx/context.md` or `.spinetx/context.json` is missing or `ready=false`, stop translating and ask the user the initial questionnaire.
4. Before translating a new chapter, read context again.
5. Use the glossary as stronger than general dictionary intuition.
6. Never use any `forbidden_targets` listed in the context.
7. After each completed chapter, update the chapter summary/open issues in context.
8. Run `spinetx validate` and fix both contract errors and context terminology errors.

## Translation workflow

From a project root:

```bash
spinetx extract .
spinetx context status .
spinetx next . --unit chunk      # next untranslated chunk
spinetx next . --unit chapter    # next incomplete chapter
spinetx next-chapter .           # same chapter workflow, explicit command
```

Open `.spinetx/context.md` first, then open each reported `.spinetx/chunks/NNNN.json`, translate each record, and write `.spinetx/translated/NNNN.json`.

After writing translations:

```bash
spinetx validate .
spinetx build .
```

If validation fails, repair the translated JSON. Do not patch the source chunk to make validation pass unless the source extraction itself is defective and the user asked for maintenance.

## Placeholder checklist before saving a translated chunk

For each record:

1. Copy the `id` exactly.
2. Translate `source` into `target`.
3. Search the source for tokens matching `__(NAME|TAG)_\d+__`.
4. Confirm every token appears in the target.
5. Confirm no additional tokens appear in the target.
6. Confirm the target is a string and not empty.
7. Confirm the final file is valid JSON, not JSON-with-comments.

A simple verification snippet for one chunk:

```python
import json, re, sys
from pathlib import Path

src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tgt = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert src["chunk_id"] == tgt["chunk_id"]
assert len(src["records"]) == len(tgt["records"])
rx = re.compile(r"__(?:NAME|TAG)_\d+__")
for s, t in zip(src["records"], tgt["records"], strict=True):
    assert s["id"] == t["id"]
    assert t["target"].strip()
    assert sorted(rx.findall(s["source"])) == sorted(rx.findall(t["target"]))
```

Prefer `spinetx validate` as the authoritative check.

## Package maintenance map

- `spinetx/models.py`: Pydantic models for source and translated JSON contracts.
- `spinetx/placeholders.py`: placeholder token creation and restoration.
- `spinetx/chunking.py`: sentence segmentation and chunk packing.
- `spinetx/markdown_io.py`: Markdown extraction and rebuild.
- `spinetx/html_io.py`: XHTML extraction and rebuild.
- `spinetx/epub_io.py`: EPUB read/extract/build wrapper around EbookLib.
- `spinetx/config.py`: project layout, config TOML, manifest, names, source discovery.
- `spinetx/validate.py`: contract validation and validation report writing.
- `spinetx/build.py`: maps translated records back to spans and rebuilds outputs.
- `spinetx/cli.py`: Typer command surface.

## Maintenance guardrails

- Keep spinetx deterministic, local, and network-free.
- Do not add automatic translation API calls to core.
- Do not change chunk IDs, record IDs, or JSON field names without migration and tests.
- Keep `spinetx extract` idempotent: it may rebuild `.spinetx/chunks`, but must not delete `.spinetx/translated`.
- Keep build/rebuild structure-preserving for Markdown and EPUB.
- Add tests before refactoring extractor internals.
- Treat `spinetx validate` as the gate before build.

## Known current maintenance priorities

- Add Python 3.10 `tomli` fallback because `tomllib` is not available in Python 3.10.
- Align CLI docs and options: `--source`/`--source-file`/`--source-lang` are currently easy to confuse.
- Prefer console script target `spinetx.cli:main`.
- Remove duplicate unreachable `return` in `spinetx/epub_io.py`.
- Consider making `spinetx build` fail on invalid present translations instead of silently using partial fallback behavior.

## Maintainer note: sentence segmentation

`spinetx` uses `phrasplit` for deterministic sentence segmentation in chunk extraction.
When editing `spinetx/chunking.py`, keep the simple backend forced with
`use_spacy=False` unless the user explicitly requests an opt-in spaCy mode.
Do not allow environment-dependent auto-detection in normal extraction.

## EPUB dependency guidance

Do not add a plain text EPUB extractor as a core dependency unless it preserves spinetx’s span-to-template mapping. A library such as `epub2text` can be useful as a reference for NAV/NCX parsing, chapter listings, page listings, metadata, or optional inspection, but spinetx core must preserve XHTML structure and placeholders for rebuild.

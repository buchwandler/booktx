# Translation context

The translation context captures user-approved decisions that cannot be enforced by the JSON shape alone.

The authoritative file is:

```text
.booktx/context.json
```

The agent-readable rendered file is:

```text
.booktx/context.md
```

`context.md` is always regenerated from `context.json`; it must never be the only durable place for chapter notes. Persist chapter notes through `booktx context chapter-note` (which writes `context.json`) instead of editing `context.md` by hand.

## Why context exists

A syntactically valid chunk can still be a bad translation if it uses the wrong style or a forbidden term. The context gate prevents translation from starting until required decisions are captured.

Example: in some fantasy settings, `Lowlander` should not be translated as `Niederländer`, because that means a person from the Netherlands in German. The seed glossary forbids that target and asks for an approved rendering.

## Readiness

`booktx next` requires the context to exist and have:

```json
"ready": true
```

The command refuses translation work while required questions are open, unless `--allow-missing-context` is explicitly used.

## Seed questions

The default context asks about:

| Id   | Topic                    | Required |
| ---- | ------------------------ | -------- |
| Q001 | Target locale            | Yes      |
| Q002 | Overall style            | Yes      |
| Q003 | Register                 | Yes      |
| Q004 | Dialogue style           | Yes      |
| Q005 | Names                    | Yes      |
| Q006 | Invented world terms     | Yes      |
| Q007 | Species/culture terms    | Yes      |
| Q008 | Honorifics               | Yes      |
| Q009 | Place/geopolitical terms | Yes      |
| Q010 | Typography               | No       |
| Q011 | Units                    | No       |
| Q012 | Glossary enforcement     | Yes      |

Required questions block `context mark-ready`.

## Answer flow

```bash
booktx context init ./book --non-interactive
booktx context questions ./book
booktx context answer ./book Q001 --text de-DE
booktx context answer ./book Q002 --text "fluent literary German"
booktx context mark-ready ./book
```

Some answers hydrate fields in the style profile:

| Question | Hydrated field             |
| -------- | -------------------------- |
| Q001     | `style.target_locale`      |
| Q002     | `style.prose_style`        |
| Q003     | `style.register`           |
| Q004     | `style.dialogue_style`     |
| Q010     | `style.punctuation_policy` |
| Q011     | `style.units_policy`       |

Other answers remain as documented decisions in the question list and should inform glossary and style edits.

## Glossary entries

A glossary entry can approve a target, forbid targets, or both.

```json
{
  "source": "Lowlander",
  "target": null,
  "forbidden_targets": ["Niederländer", "Holländer"],
  "category": "demonym",
  "status": "open",
  "notes": "Demonym for Lowlands. German target must avoid the Dutch/Nederlander meaning.",
  "examples": [],
  "case_sensitive": false,
  "enforce": "error"
}
```

Enforcement levels:

| Value   | Effect                |
| ------- | --------------------- |
| `off`   | No validation finding |
| `warn`  | Validation warning    |
| `error` | Validation error      |

Forbidden targets are checked only when the matching source term appears in the source chunk.

## Add a term

```bash
booktx context add-term ./book "Lowlands" \
  --target "Tieflande" \
  --forbid "Niederlande" \
  --forbid "Holland" \
  --category place \
  --notes "Fantasy geopolitical term." \
  --enforce error
```


## Chapter notes

Chapter notes capture per-chapter terminology, voice decisions, and open issues. They live in `context.json` under `chapter_contexts` and are rendered into the `## Chapter notes` section of `context.md`.

Add or update a note after completing a chapter:

```bash
booktx context chapter-note ./book 0006 \
  --title "TWO" \
  --source-summary "..." \
  --translation-summary "..." \
  --decision "keep Apt untranslated" \
  --open-issue "register for Beetle"
```

`--decision` and `--open-issue` are repeatable and append in order without duplicating existing values. Pass `--replace-decisions` or `--replace-open-issues` to replace a list instead of appending.

If chapter notes exist only in `context.md` (for example from a manual edit), recover them into `context.json`:

```bash
booktx context import-md ./book --write
```

`--replace-existing` replaces conflicting durable fields; `--append-existing-lists` appends decisions and open issues. The two are mutually exclusive.

## Rendering context.md

`booktx context render` is a dry run by default: it reports whether `context.md` matches the render and whether writing would be unsafe. Use `--stdout` to print the rendered Markdown, or `--write` to persist it:

```bash
booktx context render ./book --write
```

`--write` refuses to overwrite when `context.md` has chapter notes not safely represented in `context.json`. Run `booktx context import-md ./book --write` first, or pass `--write --force-discard-md-only` to discard Markdown-only notes.

## Agent obligations

Before translating every chapter or chunk, an agent must:

1. Read `.booktx/context.md`.
2. Follow approved style and glossary decisions.
3. Avoid every `forbidden_targets` value.
4. After each completed chapter, persist new terminology, voice decisions, and open issues with `booktx context chapter-note ./book CHAPTER_ID ...`, never by editing `.booktx/context.md` directly.
5. Run `booktx validate` and fix context findings as well as contract findings.

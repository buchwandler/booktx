# Translation contract

Translation state is profile-local. The durable current record store is
`translations/<profile>/translation-store.json` (`TranslationStoreV2`). The
version ledger, context, tasks, submissions, reviews, and reports are also
profile-local.

## Task metadata

`booktx translate next` records the profile, target language and locale,
translation version, context view hash, source hash, and relevant profile and
source configuration hashes. `booktx translate insert` rejects stale or
profile-mismatched submissions.

Block submissions are written under `translations/<profile>/ingest/`:

```text
# booktx block submission
# profile: PROFILE
# task: TASK_ID
# translation_version: 1.2
>>> 0001-000001
Translated text.
```

Keep each record header unchanged, write only the target text, preserve
placeholder tokens, and do not add commentary. JSON submissions use schema
version 2 and must declare the matching profile and translation version.

## Generated exports

`translations/<profile>/translated/NNNN.json` is a compatibility export. It is
derived from the store and is not the canonical state. Editor indexes, reports,
and output are likewise generated artifacts.

## Versions and reviews

`booktx translate compare` and `activate` operate on versions inside one
profile. Cross-profile comparison is explicit through `booktx profile compare`
or judge workflows. Review candidates are stored separately in the nested
`reviews` data and use `R<pass>.<run>` references. Effective output resolves a
valid review candidate before the current translation version.

## Placeholders and Markdown

Required placeholders such as `__NAME_001__` and `__TAG_001__` must be
preserved exactly. Markdown links keep their URLs, code remains opaque, and
front matter is preserved. Targets must be non-empty and record ids must not
change.

## EPUB inline XHTML

EPUB source records may use constrained inline XHTML. Targets preserve the
same tag names and attributes around translated text nodes. Do not replace
XHTML with Markdown, add block markup, change opaque inline elements, or invent
attributes. Validation and build preflight check the inline skeleton and
opaque-content preservation.

## Context and provenance

`context.json` is authoritative and `context.md` is rendered. Each task stores
an immutable effective context view under
`translations/<profile>/context-history/views/<sha>/`. Translation and review
revisions retain baseline and context-view provenance. Do not edit the store or
rendered context manually; use the CLI workflows.

## Glossary phrase collisions

Glossary enforcement uses the longest non-shadowed source span. If a shorter
term occurs inside a longer configured phrase, add the longer phrase or use
natural apposition rather than forcing an unnatural target token. A standalone
shorter occurrence remains subject to its own rule.

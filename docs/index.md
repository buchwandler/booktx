# Documentation index

booktx uses a profile-first lifecycle: initialize the source project, extract shared source state, create or select an isolated profile, approve its context, translate through profile-local task files, validate, and build the final output. The canonical state remains under `.booktx/` and `translations/<profile>/`; generated reports, translated exports, and output files are derived artifacts.

## Onboarding

1. [Quickstart](quickstart.md) — install booktx and complete the first profile workflow.
2. [Project layout](project-layout.md) — identify shared source state and profile-local mutable state.
3. [Profiles](profiles.md) — understand profile selection and isolation.

## Operating workflows

- [Commands](commands.md) — CLI reference and copyable command patterns.
- [Context](context.md) — policy questions and the required human approval gate.
- [Agent workflow](agent-workflow.md) — collaborative and isolated harness rules.
- [Series workflows](series.md) — prepare the next book and move reusable policy safely.

## Format and reference

- [Markdown](markdown.md) — Markdown extraction and rebuilding.
- [EPUB](epub.md) — EPUB-specific records, inline XHTML, and output behavior.
- [Translation contract](translation-contract.md) — invariants for accepted work.
- [Concepts](concepts.md) — terminology and state model.

## Maintenance and internals

- [Troubleshooting](troubleshooting.md) — actionable remediation for common failures.
- [Development](development.md) — tests, linting, and documentation checks.
- [Architecture](architecture.md) and [canonical store split](architecture/canonical-store-split.md) — implementation boundaries.
- [API](api.md) and [mypy baseline](mypy-baseline.md) — generated/reference material.

The normal lifecycle is:

1. Initialize a source project and extract source chunks into `.booktx/chunks/`.
2. Create or select a translation profile.
3. Build and review `translations/<profile>/context.json` and `context.md`; wait for user approval before marking context ready.
4. Translate through `translations/<profile>/ingest/`.
5. Validate the selected profile and build into `translations/<profile>/output/`.

```{toctree}
   :maxdepth: 2

quickstart
project-layout
profiles
concepts
commands
context
series
agent-workflow
translation-contract
markdown
epub
architecture
architecture/canonical-store-split
api
development
mypy-baseline
troubleshooting
```

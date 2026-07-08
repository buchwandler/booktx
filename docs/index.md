# Documentation index

Start here:

1. Read [quickstart](quickstart.md) for the profile-first workflow.
2. Read [project layout](project-layout.md) for shared vs profile-local state.
3. Read [profiles](profiles.md) for the isolation model.
4. Read [commands](commands.md) for CLI usage.
5. Read [context](context.md) before working on translations.
6. Read [series](series.md) for repeated next-book setup.
7. Read [agent workflow](agent-workflow.md) for coding-agent operating rules.

8. Initialize a source project.
9. Extract source chunks into `.booktx/chunks/`.
10. Create or select a translation profile.
11. Build or approve `translations/<profile>/context.json` and `context.md`.
12. Translate via `translations/<profile>/ingest/`.
13. Validate the selected profile.
14. Build the final document into `translations/<profile>/output/`.

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

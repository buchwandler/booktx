# Documentation index

Start here:

1. Read [quickstart](quickstart.md) for the profile-first workflow.
2. Read [project layout](project-layout.md) for shared vs profile-local state.
3. Read [profiles](profiles.md) for the isolation model.
4. Read [commands](commands.md) for CLI usage.
5. Read [context](context.md) before working on translations.
6. Read [agent workflow](agent-workflow.md) for coding-agent operating rules.

7. Initialize a source project.
8. Extract source chunks into `.booktx/chunks/`.
9. Create or select a translation profile.
10. Build or approve `translations/<profile>/context.json` and `context.md`.
11. Translate via `translations/<profile>/ingest/`.
12. Validate the selected profile.
13. Build the final document into `translations/<profile>/output/`.

```{toctree}
:maxdepth: 2

quickstart
project-layout
profiles
concepts
commands
context
agent-workflow
translation-contract
markdown
epub
architecture
api
development
troubleshooting
```

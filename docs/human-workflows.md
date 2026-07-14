# Human workflows

This guide focuses on human decisions and lifecycle outcomes rather than the
low-level agent task protocol.

## Start a book

```bash
booktx init ./book --source-file ./book.epub --source-lang en
booktx extract ./book
booktx chapters ./book --audit
booktx profile create ./book PROFILE --target de --target-locale de-DE --model MODEL
booktx guide ./book --profile PROFILE
```

## Approve policy

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx source analyze ./book --write --sync-profiles
booktx context questionnaire ./book --profile PROFILE --stdout
booktx context approve ./book --profile PROFILE Q001 --text "..." --approved-by "user:NAME"
booktx context mark-ready ./book --profile PROFILE
booktx glossary mandate ./book "Empire" --profile PROFILE --target "Imperium" --forbid "Reich"
```

Generated recommendations never replace human approval. Review the context and
terminology decisions before marking the profile ready.

## Prepare an isolated workspace

```bash
booktx agents write ./book --mode isolated --profile PROFILE
```

Start the harness in `translations/PROFILE/`. Project-root administration and
cross-profile comparison remain outside the isolated workflow.

To start a bounded translation run in one command from the profile root:

```bash
booktx translate todo-next . --chapters 3 --batch-words 800 --write --resume --format block
```

The generated task contract is authoritative: read `tasks/TASK.agent.md` first,
edit only `ingest/TASK.block.txt`, lint before the first insert, and submit only
after lint passes.

## Prepare the next series book

```bash
booktx series prepare ./book5 --source-file ./book5.epub \
  --from-book ./book4 --profile PROFILE --series-id series-id \
  --title "Series policy" --target de --target-locale de-DE --model MODEL --write
booktx context questionnaire ./book5 --profile PROFILE --stdout
```

Review the generated policy before running `context mark-ready`.

## Quality workflows

Configure a review pass with `booktx review configure` and inspect it with
`booktx review status`. Prepare a comparison or revision profile with
`booktx judge create-profile`, then use `booktx judge prepare-isolation` or
`booktx judge prepare-grammar` before agent work.

## Verify output

```bash
booktx check ./book --profile PROFILE --fail-on-warnings
booktx validate ./book --profile PROFILE --fail-on-warnings
booktx build ./book --profile PROFILE --require-complete
```

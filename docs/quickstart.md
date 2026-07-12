# Quickstart

This is the human operator path. It stops before translation until required
policy decisions are approved.

Project-root profile commands use `--profile PROFILE`. Inside
`translations/PROFILE/`, use `.` and the validated profile marker resolves the
profile.

## 1. Initialize and extract

```bash
booktx init ./demo --source-file ./book.epub --source-lang en
booktx extract ./demo
booktx chapters ./demo --audit
```

## 2. Create a profile

```bash
booktx profile create ./demo PROFILE \
  --target de --target-locale de-DE --model MODEL
booktx guide ./demo --profile PROFILE
```

## 3. Prepare and review context

```bash
booktx context init ./demo --profile PROFILE --non-interactive
booktx source analyze ./demo --write --sync-profiles
booktx source interview-plan ./demo --profile PROFILE --write
booktx source interview-next ./demo --profile PROFILE --format markdown
booktx context questionnaire ./demo --profile PROFILE --stdout
```

Recommendations and questionnaire output are not approval. Show them to the
human and wait for an explicit decision.

## 4. Approve and prepare the agent

```bash
booktx context approve ./demo --profile PROFILE Q001 \
  --text "<USER_APPROVED_TEXT>" --approved-by "user:NAME"
booktx context mark-ready ./demo --profile PROFILE
booktx glossary mandate ./demo "Empire" --profile PROFILE \
  --target "Imperium" --forbid "Reich"
booktx agents write ./demo --mode isolated --profile PROFILE
```

Start the harness inside `demo/translations/PROFILE/`.

## 5. Run and build

From the profile root:

```bash
booktx guide .
booktx status .
booktx translate next . --unit batch --max-words 800 --format block
booktx translate insert . --task-id TASK --file ingest/TASK.block.txt --format block
booktx check .
booktx validate .
booktx build .
```

Use `booktx translate todo-next` and `todo-resume` for bounded multi-chapter
runs. Do not edit the profile store or rendered context directly.

## 6. Continue with the guides

- [Project layout](project-layout.md) describes shared and profile-local paths.
- [Profiles](profiles.md) describes runtime resolution and isolation.
- [Context](context.md) describes policy approval and provenance.
- [Commands](commands.md) is the current CLI reference.
- [Agent workflow](agent-workflow.md) describes durable task work.
- [Series workflows](series.md) prepares the next book with a review stop.
- [Markdown](markdown.md) and [EPUB](epub.md) describe format behavior.
- [Troubleshooting](troubleshooting.md) maps failures to safe remediation.

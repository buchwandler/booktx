# Quickstart

This quickstart is for the **human operator**. It stops at the points where the
human must approve policy or start the isolated coding-agent harness.

There is **no global profile selection**. From the project root, pass
`--profile PROFILE` for profile-specific work. From `translations/PROFILE/`,
booktx resolves the current profile root automatically.

## 1. Create the source project

```bash
booktx init ./demo --source-file ./book.epub --source-lang en
```

`booktx init` creates the source-first project layout and prints the next human
step.

## 2. Extract the source and inspect chapters

```bash
booktx extract ./demo
booktx chapters ./demo --audit
```

For EPUB sources, review the chapter audit before creating profile-local work.

## 3. Create a translation profile

```bash
booktx profile create ./demo de_glm_5_2 \
  --target de \
  --target-locale de-DE \
  --model zai/glm-5.2@high
```

This creates the profile only. It does **not** create a global profile selection.

## 4. Ask booktx for the next human step

```bash
booktx guide ./demo --profile de_glm_5_2
```

Use `guide` whenever you return to a project and want one canonical next action.

## 5. Initialize context and review source policy

```bash
booktx context init ./demo --profile de_glm_5_2 --non-interactive
booktx source analyze ./demo --write --sync-profiles
booktx source interview-plan ./demo --profile de_glm_5_2 --write
booktx source interview-next ./demo --profile de_glm_5_2 --format markdown
booktx context questionnaire ./demo --profile de_glm_5_2 --stdout
```

Generated recommendations are **not** approvals. A human must approve policy
before translation begins.

## 6. Record approved context and mark it ready

```bash
booktx context approve ./demo \
  --profile de_glm_5_2 \
  Q001 \
  --text "<USER_APPROVED_TEXT>" \
  --approved-by "user:<USER>"

booktx context mark-ready ./demo --profile de_glm_5_2
```

Use the `glossary` surface for binding terminology decisions:

```bash
booktx glossary mandate ./demo "Empire" \
  --profile de_glm_5_2 \
  --target "Imperium" \
  --forbid "Reich"
```

## 7. Prepare the isolated agent workspace

```bash
booktx agents write ./demo --mode isolated --profile de_glm_5_2
```

Then start the harness inside:

```text
demo/translations/de_glm_5_2/
```

## 8. Monitor progress

```bash
booktx status ./demo --profile de_glm_5_2
booktx guide ./demo --profile de_glm_5_2
```

`status` shows translation progress plus the current human and agent actions.

## 9. Check and build

```bash
booktx check ./demo --profile de_glm_5_2
booktx build ./demo --profile de_glm_5_2
```

Then review the generated context and finish the human gate:

```bash
booktx context questionnaire ./book5 --profile de_glm_5_2 --stdout
booktx context status ./book5 --profile de_glm_5_2
booktx context render ./book5 --profile de_glm_5_2 --write
booktx context mark-ready ./book5 --profile de_glm_5_2
booktx agents write ./book5 --mode isolated --profile de_glm_5_2
```

Advanced/manual path:

1. Export a context pack from the completed profile.
2. Initialize and extract the new book, then create the matching profile.
3. Run `booktx context import-pack` as a dry run, then re-run with `--write`.
4. Run `booktx source analyze --write --sync-profiles`.
5. Run `booktx context prefill --from-source-analysis --consolidate-imported-policy --write`.
6. Review the context questionnaire, then mark ready and write isolated agent instructions.

## Continue with the guides

After the first build, use the guide for the next task:

- [Project layout](project-layout.md) for shared source state and profile-local mutable state.
- [Profiles](profiles.md) for isolation, profile selection, and version boundaries.
- [Context](context.md) for policy questions and the required approval gate.
- [Agent workflow](agent-workflow.md) for collaborative or isolated harness operation.
- [Series workflows](series.md) when preparing another book from an existing profile.
- [Markdown](markdown.md) or [EPUB](epub.md) for format-specific behavior.
- [Commands](commands.md) for the complete CLI reference.
- [Troubleshooting](troubleshooting.md) for common failures and safe remediation.
- [Development](development.md) for tests and the local documentation build.

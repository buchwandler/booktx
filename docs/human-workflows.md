# Human workflows

This guide is for the **human operator**. It focuses on the supported workflow
outcomes instead of the low-level task protocol.

## Start a new book

```bash
booktx init ./book --source-file ./book.epub --source-lang en
booktx extract ./book
booktx chapters ./book --audit
booktx profile create ./book PROFILE --target de --target-locale de-DE --model MODEL
booktx guide ./book --profile PROFILE
```

## Approve source policy and context

```bash
booktx context init ./book --profile PROFILE --non-interactive
booktx source analyze ./book --write --sync-profiles
booktx source interview-plan ./book --profile PROFILE --write
booktx source interview-next ./book --profile PROFILE --format markdown
booktx context questionnaire ./book --profile PROFILE --stdout
booktx context approve ./book --profile PROFILE Q001 --text "..." --approved-by "user:<USER>"
booktx context mark-ready ./book --profile PROFILE
```

## Manage binding terminology

```bash
booktx glossary status ./book --profile PROFILE
booktx glossary mandate ./book "Empire" --profile PROFILE --target "Imperium" --forbid "Reich"
booktx glossary audit ./book "Empire" --profile PROFILE
```

## Prepare an isolated agent workspace

```bash
booktx agents write ./book --mode isolated --profile PROFILE
```

Then start the harness in `translations/PROFILE/`.

## Start the next series book

```bash
booktx series recipe write ./book4 \
  --profile de_glm_5_2 \
  --series-id series-id \
  --title "Series context" \
  --output ./series.toml

booktx series prepare ./book5 \
  --source-file ./book5.epub \
  --from-book ./book4 \
  --recipe ./series.toml \
  --write
```

## Prepare a grammar-only revision profile

```bash
booktx judge prepare-grammar ./book \
  --source-profile PROFILE \
  --profile PROFILE_GRAMMAR \
  --model MODEL \
  --write
```

## Verify and build

```bash
booktx check ./book --profile PROFILE
booktx build ./book --profile PROFILE
```

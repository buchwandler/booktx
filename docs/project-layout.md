# Project layout

`booktx` uses a source-first layout with profile-local mutable state:

```text
book/
  source/book.epub
  .booktx/
    source-config.toml
    source-manifest.json
    names.json
    chapter-map.json
    chunks/
    reports/
  translations/PROFILE/
    .booktx-profile.json
    config.toml
    identity.json
    context.json
    context.md
    context-history/views/<sha>/
    translation-store/
      manifest.json
      current/
      translation-candidates/
      review-candidates/
      transactions/
    translation-version-ledger.json
    tasks/
    todos/
    ingest/
    reviews/
    review-todos/
    translated/
    reports/
    output/
```

## Shared source state

`.booktx/` contains only source configuration and source-derived evidence:

| Path                           | Meaning                                     |
| ------------------------------ | ------------------------------------------- |
| `.booktx/source-config.toml`   | Source language, file, format, and chunking |
| `.booktx/source-manifest.json` | Source digest and extraction manifest       |
| `.booktx/names.json`           | Protected source names                      |
| `.booktx/chapter-map.json`     | Chapter and record mapping                  |
| `.booktx/chunks/`              | Extracted source records                    |
| `.booktx/reports/`             | Source and chapter audit reports            |

## Profile-local state

Each `translations/<profile>/` directory is an isolation boundary. The
`translation-store/` directory is the canonical store backend; `context.json`
and the version ledger are also durable state. Tasks, todos, submission files,
reviews, judge artifacts, and reports remain profile-local.

`translated/`, editor indexes, and `output/` are generated artifacts. They are
rebuildable from source state and the profile store and are not the source of
truth.

## Resolution and safety

From the project root, profile-local commands require `--profile PROFILE`.
From a profile root, booktx resolves the profile through
`.booktx-profile.json` and the validated `config.toml`, so commands use `.`.
The marker also binds the profile to the current project source identity.

## Legacy layout

A legacy single-layout project may still contain translation files under
`.booktx/`. Migrate it with:

```bash
booktx profile migrate-current ./book PROFILE
```

After migration, `.booktx/` keeps shared source state and profile-local mutable
files move to `translations/PROFILE/`.

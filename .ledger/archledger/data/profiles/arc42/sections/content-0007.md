---
schema_version: 4
id: content-0007
kind: content
type: section
section: deployment_view
title: Deployment View
order: 70
status: accepted
version: 2
body_format: markdown
---

## Deployment Context

booktx is a Python CLI tool distributed as a pip-installable wheel. It has no server, no daemon, and no network service. Deployment means installing the package into a Python environment.

### Installation

```bash
pip install booktx
```

Or from source:

```bash
pip install .
# or: pip install -e .[dev,docs]
```

### Runtime Dependencies

booktx requires Python >= 3.10 and the following core dependencies:

- `typer` — CLI framework
- `rich` — Console output formatting
- `pydantic>=2` — Data validation
- `tomli-w` / `tomli` — TOML write/read
- `beautifulsoup4` — EPUB XHTML parsing
- `markdown-it-py` — Markdown parsing
- `phrasplit>=0.3.3` — Sentence splitting
- `epub2text>=0.2.7` — EPUB extraction
- `text2epub>=0.1.4` — EPUB generation

Optional: `spacy>=3.7,<4` for source analysis features.

### Filesystem Layout

```text
~/.config/booktx/translation-termbase/  # User-global termbase shards
                                           # (override with BOOKTX_TERMBASE_DIR)
```

### Environments

| Environment  | Python Versions | Purpose                                               |
| ------------ | --------------- | ----------------------------------------------------- |
| Development  | 3.10+           | Local editing, tests, linting                         |
| CI (PR)      | 3.10, 3.13      | Quality gate, import-health checks, wheel smoke tests |
| CI (Release) | 3.13            | Build + publish to PyPI                               |
| Production   | 3.10+           | End-user pip install                                  |

### CI/CD

- **Quality gate** (`scripts/quality_gate.py`): compile checks, focused tests, full pytest, Ruff, mypy, wheel build, clean-environment install, CLI help smoke tests. Stops at first failure.
- **GitHub Actions**: PR and release-branch workflows enforce the gate on Python 3.10 and 3.13.
- **Publish**: `python-publish.yml` requires the quality gate for the exact checked-out commit before publishing to PyPI.

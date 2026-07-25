---
schema_version: 4
id: block-0050
kind: block
type: black_box
title: Bootstrap (bootstrap.py)
status: accepted
section: building_block_view
level: 1
parent: null
order: 140
version: 2
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
---

Lazy console entry point referenced by pyproject.toml [project.scripts]. Wraps booktx.cli:main in try/except. On import failure, renders concise diagnostics with exit code 70, project-data safety message, troubleshooting commands, and BOOKTX_DEBUG=1 opt-in for full traceback.

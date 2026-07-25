---
schema_version: 4
id: quality-0020
kind: quality
type: quality_goal
title: Lazy Startup Robustness
status: accepted
section: introduction_and_goals
order: 40
version: 4
priority: 4
scenario:
  A syntax error in command_catalog.py causes import failure; booktx renders
  exit code 70 with actionable message
body_format: markdown
---

On import failure, booktx's bootstrap layer renders concise diagnostics with exception class and message, exit code 70, project-data safety message, and troubleshooting commands. BOOKTX_DEBUG=1 opt-in prints the full traceback. Malformed command catalog metadata emits one warning and falls back to native Typer help.

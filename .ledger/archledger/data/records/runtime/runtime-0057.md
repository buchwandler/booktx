---
schema_version: 4
id: runtime-0057
kind: runtime
type: runtime_scenario
title: CLI Startup Error Handling
status: accepted
section: runtime_view
order: 50
version: 5
participants:
  - User
  - booktx bootstrap
  - booktx CLI
trigger: CLI invocation (any command)
result: Normal CLI execution or exit code 70 with diagnostics
body_format: markdown
---

On CLI invocation: 1. Python loads booktx.bootstrap:main. 2. bootstrap.main() tries to import booktx.cli.main. 3. On import error: renders concise message with exception class/message, exit code 70, project-data safety statement, troubleshooting commands, and BOOKTX_DEBUG hint. 4. On success: delegates to cli.main() for normal operation. 5. Malformed command catalog metadata emits one warning and falls back to native Typer help.

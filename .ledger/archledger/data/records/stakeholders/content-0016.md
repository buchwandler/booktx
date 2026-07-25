---
schema_version: 4
id: content-0016
kind: content
type: stakeholder
title: Tool Integrators
status: accepted
section: introduction_and_goals
order: 40
version: 3
contact: ""
expectations:
  - Stable CLI interface
  - Documented data models
  - Machine-readable --json output
body_format: markdown
---

Tool integrators consume booktx via its stable CLI and documented JSON models. The --json flag on status and other commands provides machine-readable output. Pydantic models in models.py define the complete data contract. No REST API or plugin system is provided.

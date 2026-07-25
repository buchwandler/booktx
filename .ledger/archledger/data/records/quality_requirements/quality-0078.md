---
schema_version: 4
id: quality-0078
kind: quality
type: quality_requirement
title: Startup Robustness
status: accepted
section: quality_requirements
order: 40
version: 3
category: reliability
source: ""
measure: ""
scenarios: []
body_format: markdown
---

booktx CLI must produce exit code 70 on import failure, render actionable diagnostics, and never modify project data during startup. Measurement: test_bootstrap.py verifies exit codes, message format, and BOOKTX_DEBUG behavior.

---
schema_version: 4
id: runtime-0056
kind: runtime
type: runtime_scenario
title: Judge Workflow
status: accepted
section: runtime_view
order: 40
version: 5
participants:
  - User/AI Agent
  - booktx CLI
  - Filesystem (source + selection stores)
trigger: judge task-next command
result: JudgeTask delivered; on submit, selection decision recorded
body_format: markdown
---

1. User runs 'booktx judge task-next --profile judge_profile --sources sourceA sourceB'. 2. booktx loads source store and selection profiles, builds JudgeTask with candidate evidence. 3. Agent evaluates candidates. 4. Agent runs 'booktx judge submit <task-id>' with decisions. 5. booktx records decisions in selection-ledger.json.

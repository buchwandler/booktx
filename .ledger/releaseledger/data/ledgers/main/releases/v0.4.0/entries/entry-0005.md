---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 2
entry_id: entry-0005
release_version: v0.4.0
kind: added
summary:
  Added same-book multi-profile context sync for merging glossary and context
  sections between sibling profiles
status: accepted
audience: null
scopes: []
source_refs:
  - git:689061e7369aea04d2d9f0618bd98c7870d0eaee
  - git:77b2d3243536a7bc79e60331dae34ac400e05723
paths:
  - booktx/context_sync.py
  - booktx/commands/context.py
  - booktx/workflows/context.py
  - booktx/commands/termbase.py
  - booktx/workflows/termbase.py
  - docs/commands.md
  - docs/context.md
  - docs/profiles.md
issues: []
prs: []
sources:
  - git:689061e7369aea04d2d9f0618bd98c7870d0eaee
  - git:77b2d3243536a7bc79e60331dae34ac400e05723
breaking: false
internal: false
order: 5
---

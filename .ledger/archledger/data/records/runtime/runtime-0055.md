---
schema_version: 4
id: runtime-0055
kind: runtime
type: runtime_scenario
title: Build Output
status: accepted
section: runtime_view
order: 30
version: 5
participants:
  - User
  - booktx CLI
  - Filesystem (store, output)
trigger: build command
result: Translated document written to output directory
body_format: markdown
---

1. User runs 'booktx build --profile de_default'. 2. For each record, booktx resolves effective candidate: prefer active TranslationReviewCandidate if chain-valid, else fall back to active TranslationCandidate. 3. booktx restores protected placeholders. 4. booktx assembles target document (Markdown or EPUB XHTML). 5. Output written to translations/<profile>/output/.

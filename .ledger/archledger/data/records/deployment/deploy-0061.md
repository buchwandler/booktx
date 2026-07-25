---
schema_version: 4
id: deploy-0061
kind: deploy
type: infrastructure
title: End-User Installation
status: accepted
section: deployment_view
level: 1
parent: null
order: 40
version: 3
environment: production
maps_building_blocks:
  - block-0049
  - block-0050
body_format: markdown
---

End users install via pip install booktx. Requires Python >= 3.10. User-global termbase stored in ~/.config/booktx/translation-termbase/ (override via BOOKTX_TERMBASE_DIR). No configuration needed beyond pip install.

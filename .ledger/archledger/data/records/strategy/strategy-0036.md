---
schema_version: 4
id: strategy-0036
kind: strategy
type: strategy_item
title: Technology Stack
status: accepted
section: solution_strategy
order: 50
version: 2
drivers: []
constraints: []
related_adrs: []
body_format: markdown
---

Typer + Rich for mature CLI with structured console output. Pydantic v2 for strict JSON boundary validation. markdown-it-py for CommonMark-compliant parsing. BeautifulSoup 4 for EPUB XHTML. Hatchling + hatch-vcs for PEP 621 builds. Synchronous I/O throughout (no async needed for filesystem operations).

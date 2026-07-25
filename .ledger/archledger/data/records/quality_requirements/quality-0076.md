---
schema_version: 4
id: quality-0076
kind: quality
type: quality_requirement
title: Profile Isolation Guarantee
status: accepted
section: quality_requirements
order: 20
version: 3
category: security
source: ""
measure: ""
scenarios: []
body_format: markdown
---

No command operating on profile A may read or write profile B's state. Cross-profile operations must explicitly declare all participating profiles. Measurement: code review confirms all store/context paths derive from a single Project.profile_dir.

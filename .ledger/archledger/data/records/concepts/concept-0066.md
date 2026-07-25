---
schema_version: 4
id: concept-0066
kind: concept
type: concept
title: Profile Isolation
status: accepted
section: cross_cutting_concepts
order: 50
version: 2
applies_to: []
body_format: markdown
---

Profiles are hard-isolated leaf directories. A build or validation run resolves exactly one profile. The only cross-profile operations are explicit comparison commands (profile compare, judge). No profile reads another profile's store during normal operations.

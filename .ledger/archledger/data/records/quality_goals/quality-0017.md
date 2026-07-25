---
schema_version: 4
id: quality-0017
kind: quality
type: quality_goal
title: Profile Isolation
status: accepted
section: introduction_and_goals
order: 10
version: 4
priority: 1
scenario: A build for profile de_default never accesses translations/fr_default/ state
body_format: markdown
---

One profile's translation, judge, and review state must never read or mutate another profile's state. Cross-profile operations (profile compare, judge) must explicitly declare all participating profiles. Path construction in config.py always derives from a single Project.profile_dir.

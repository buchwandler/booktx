---
schema_version: 4
id: concept-0068
kind: concept
type: concept
title: Todo Lifecycle
status: accepted
section: cross_cutting_concepts
order: 70
version: 2
applies_to: []
body_format: markdown
---

TranslationTodo and ReviewTodo are durable run-control artifacts for bounded multi-chapter/-pass agent runs. They carry scope, stop conditions, starting totals, and chapter lists. A companion TranslationTodoLifecycle tracks mutable state (open/completed/abandoned/superseded) separately from the immutable todo.

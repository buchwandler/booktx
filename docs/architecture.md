# Architecture

## Current data flow

```text
source document
  -> booktx extract
  -> .booktx/chunks/*.json and source manifests
  -> selected profile context and canonical translation store
  -> translations/<profile>/translation-store/
  -> booktx validate/check
  -> generated translated exports and reports
  -> booktx build
  -> translations/<profile>/output/
```

## Boundaries

The source boundary contains the source file, extraction configuration,
protected names, chapter metadata, chunks, and source-analysis evidence. The
profile boundary contains target language and locale, identity, context,
translation versions, review candidates, tasks, submissions, validation
reports, and output.

The profile boundary is the hard mutable-state isolation boundary. A build or
validation run resolves one profile and never reads another profile's store.
Cross-profile comparison is explicit through `booktx profile compare` or judge
workflows.

## Runtime resolution

At a project root, `--profile PROFILE` selects the profile for a command. At a
profile root, `.booktx-profile.json` is validated against the enclosing project,
profile configuration, target locale, and source identity. The runtime then
uses the marker-bound profile and brokers access to shared source data.

## Store and provenance

New profiles currently default to the v2 canonical store. The shard-based v3
store under `translations/<profile>/translation-store/` remains an explicit
opt-in migration target until its stabilization gate is completed. When v3 is
active it stores a manifest plus per-chunk current, translation-candidate, and
review-candidate shards. `TranslationStoreV2` remains the compatibility
materialization model returned by the Python loader surface. Effective output
still chooses a valid review candidate before the current translation version.
Task context views and revision metadata preserve the source, baseline, and
policy evidence needed to validate provenance.

Generated compatibility exports, indexes, reports, and output are derived from
the canonical store and can be regenerated. Agents and operators should inspect
generated indexes or CLI output, not edit shard files directly.

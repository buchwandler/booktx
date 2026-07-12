# Architecture

## Current data flow

```text
source document
  -> booktx extract
  -> .booktx/chunks/*.json and source manifests
  -> selected profile context and TranslationStoreV2
  -> translations/<profile>/translation-store.json
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

The current persisted record model is `TranslationStoreV2` in
`translations/<profile>/translation-store.json`. Records contain translation
versions and nested review candidates. Effective output chooses a valid review
candidate before the current translation version. Task context views and
revision metadata preserve the source, baseline, and policy evidence needed to
validate provenance.

Generated compatibility exports, indexes, reports, and output are derived from
this store and can be regenerated. The documentation does not describe a
separate canonical v3 store or candidate-shard layout because that design is
not implemented.

# EPUB handling

EPUB support adapts `epub2text` extraction and `text2epub` rebuilding through
`booktx.epub_io`, `booktx.epub_manifest`, and `booktx.build`.

## Extraction

`booktx extract` records raw document offsets, inline runs, source spans,
protected names, and navigation metadata in `.booktx/source-manifest.json`.
Fresh EPUB chunks contain clean block text and name placeholders. The manifest
also stores the source checksum, text2epub manifest, span references, and
chapter mapping.

Re-extract when the source checksum changes or when an old manifest lacks the
current block annotations. `booktx chapters PROJECT --audit` reports visible
TOC entries that are missing, unmapped, or only partially represented by the
chapter map.

## Rebuild and output policy

Build verifies the extraction checksum and assembles record targets back into
manifest spans. The build is transactional: a failed rebuild or output-policy
audit leaves the last successful output untouched.

Target translation builds resolve the target language for OPF and XHTML
metadata and can inject deterministic hyphenation CSS. The policy is configured
under `[epub_output]`:

```toml
[epub_output]
language_policy = "target"  # target | source | preserve | explicit
language = "de-DE"          # required for explicit
hyphenation = "auto"        # auto | manual | none | preserve
inject_css = true
patch_body_language = false
```

The language and CSS policy is deterministic, but actual hyphenation depends on
the reading system and its dictionaries. `hyphenation = "none"` disables the
generated automatic hyphenation policy. Output reports retain replacement and
unresolved-token counters and include the resolved EPUB policy and warnings.

Pass-through profiles are generated reconstruction checks. Compare their
output with an EPUB diff tool or the repository's reconstruction tests. The
documentation does not promise byte identity for an arbitrary EPUB fixture.

## Inline XHTML contract

EPUB records with inline markup use `source_markup="epub-inline-xhtml:v1"`.
Changed targets are parsed and sanitized by `epub_preflight`, then passed to
`text2epub` with inline XHTML enabled. Unchanged records use the source fragment
for identity reconstruction.

A changed target must:

- preserve the source tag sequence and attributes;
- change only visible text nodes;
- preserve opaque elements such as `code`, `img`, `svg`, `math`, and media;
- avoid comments, processing instructions, scripts, styles, block tags, event
  handlers, new attributes, and new inline elements.

Validation, `booktx check`, `translate insert`, and build use the same
build-grade preflight. Findings include `inline_xhtml_preserved`,
`inline_xhtml_no_new_attributes`, `inline_xhtml_no_block_tags`,
`inline_xhtml_opaque_preserved`, and parse or empty-visible-text errors.
`booktx translate audit-inline` lists stored records requiring attention.

## Chapter audit

The chapter map combines upstream block annotations, heading sequences, and
TOC document boundaries. The audit distinguishes:

- visible TOC chapters;
- extracted spine documents;
- chapters covered by the chapter map.

An extracted TOC target with no chapter-map boundary is an error and blocks new
chapter work. Missing or partial preview navigation is reported as a warning.
Use:

```bash
booktx chapters ./book --audit
booktx chapters ./book --audit --json
booktx epub inspect ./book --profile PROFILE
```

## Common recovery

- On a legacy manifest, run `booktx extract ./book` again.
- On a source checksum mismatch, restore the source or re-extract intentionally.
- On an unresolved placeholder, repair the submission and validate again.
- On an inline-XHTML finding, restore the source skeleton and opaque content.
- On a TOC audit error, inspect the source EPUB rather than synthesizing empty
  chapters.

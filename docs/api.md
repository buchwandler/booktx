# API reference

This page is generated reference material for selected internal Python modules.
The CLI and its documented JSON output are the user-facing interface. Python
module names and function signatures may change between releases unless a
separate API contract says otherwise.

The durable model names currently include `TranslationStoreV2`, translation
records and review candidates, context models, profile configuration, source
manifests, and EPUB span metadata. The profile-local store is
`translations/<profile>/translation-store.json`.

The reference is intentionally broad so generated documentation can expose
model fields and service helpers. It should not be read as a promise that every
imported symbol is a supported public API.

```{eval-rst}
.. automodule:: booktx.config
   :members:
   :undoc-members:

.. automodule:: booktx.models
   :members:
   :undoc-members:

.. automodule:: booktx.context
   :members:
   :undoc-members:

.. automodule:: booktx.chunking
   :members:
   :undoc-members:

.. automodule:: booktx.placeholders
   :members:
   :undoc-members:

.. automodule:: booktx.markdown_io
   :members:
   :undoc-members:

.. automodule:: booktx.epub_io
   :members:
   :undoc-members:

.. automodule:: booktx.epub_manifest
   :members:
   :undoc-members:

.. automodule:: booktx.chapters
   :members:
   :undoc-members:

.. automodule:: booktx.validate
   :members:
   :undoc-members:

.. automodule:: booktx.build
   :members:
   :undoc-members:

.. automodule:: booktx.status
   :members:
   :undoc-members:

.. automodule:: booktx.tasks
   :members:
   :undoc-members:

.. automodule:: booktx.submissions
   :members:
   :undoc-members:

.. automodule:: booktx.acceptance
   :members:
   :undoc-members:

.. automodule:: booktx.rendering
   :members:
   :undoc-members:

.. automodule:: booktx.io_utils
   :members:
   :undoc-members:

.. automodule:: booktx.record_refs
   :members:
   :undoc-members:

.. automodule:: booktx.translation_store
   :members:
   :undoc-members:

.. automodule:: booktx.versioning
   :members:
   :undoc-members:
```

# API reference

This page exposes the internal modules through Sphinx autodoc. The public command-line interface is more stable than the Python internals.

## Stability notes

- **Stable public API**: The CLI commands (`booktx init`, `booktx extract`, `booktx translate next`, etc.) and their JSON output shapes are the primary stable interface.
- **Stable models**: Pydantic models in `booktx.models` (Chunk, Record, TranslationStore, TranslationTask, Manifest) are serialization contracts used by the CLI and external tools.
- **Service modules**: `booktx.status`, `booktx.tasks`, `booktx.submissions`, `booktx.acceptance`, `booktx.rendering`, `booktx.io_utils` contain the extracted service logic. Their public functions are stable within a release cycle.
- **Internal helpers**: `booktx.config`, `booktx.context`, `booktx.validate`, `booktx.build`, `booktx.chunking`, `booktx.placeholders` are intended stable but may change between minor releases.
- **Legacy**: `booktx.html_io` contains shared XHTML helpers that may be consolidated in future releases.

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
```

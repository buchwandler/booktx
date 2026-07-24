# Releasing

booktx must pass the canonical quality gate before a package is published or
installed into the translation harness. The release checkout must be clean so
the tested wheel corresponds to the exact checked-out commit.

From the repository root, run:

```bash
python scripts/quality_gate.py \
  --require-clean \
  --artifact-dir dist \
  --evidence-file /tmp/booktx-quality-gate.json
```

Do not rebuild the package after this command. Publish the wheel and source
distribution already in `dist/`. Preserve the evidence with the release
record, including:

- the commit SHA;
- the Python version;
- the wheel filename and SHA-256;
- the quality-gate result; and
- the temporary installation target used for CLI smoke tests.

The release workflow runs this gate before its publish step. A failed gate or
dirty checkout must stop publication. The wheel smoke tests invoke the
installed `booktx --help`, `booktx mode --help`, and
`booktx translate todo-doctor --help` commands, so a source-only import check
is not sufficient release evidence.

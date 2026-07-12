# Mypy status

This page records the current type-check status. It is not a claim that the
repository has a clean type baseline.

Run the check from the repository root:

```bash
python -m mypy booktx
```

At the documentation audit baseline on 2026-07-12, this command reported 50
errors in 15 files and exited non-zero. The failures are existing typing work,
not documentation acceptance criteria. They must remain visible in task and
release reports; documentation must not describe the check as passing until an
actual run exits zero.

The project may retain targeted `# type: ignore[...]` comments where they are
already justified by third-party stubs or lazy integration boundaries. Such
comments are implementation details, not a promise that the complete package
passes mypy. Add or remove them only with the relevant type-check evidence.

For documentation work, record the exact command, exit code, and error count
from the current environment. Do not reproduce a phase-specific external
review, call this page a clean baseline, or infer success from an earlier run.

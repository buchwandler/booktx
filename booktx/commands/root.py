"""Root command app exports for Phase 3 slice 8.

The implementation lives in booktx.workflows.root so this command module stays
within the static boundary guard.
"""

from __future__ import annotations

from booktx.workflows.root import doctor_app, root_app

__all__ = ["root_app", "doctor_app"]

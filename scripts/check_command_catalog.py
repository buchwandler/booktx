"""Statically validate string-only command catalog literals.

This check deliberately parses the source without importing ``booktx`` so it
can diagnose an import-time catalog failure before the test suite collects CLI
modules.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _is_string_dict_annotation(annotation: ast.expr) -> bool:
    return ast.unparse(annotation) == "dict[str, str]"


def validate_catalog_source(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "SUMMARY_OVERRIDES" or not _is_string_dict_annotation(
            node.annotation
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            findings.append(
                f"{path}:{node.lineno}: SUMMARY_OVERRIDES must be a dict literal"
            )
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                findings.append(
                    f"{path}:{getattr(key, 'lineno', node.lineno)}: "
                    "SUMMARY_OVERRIDES keys must be string literals"
                )
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                key_text = ast.unparse(key) if key is not None else "<missing>"
                findings.append(
                    f"{path}:{getattr(value, 'lineno', node.lineno)}: "
                    f"SUMMARY_OVERRIDES[{key_text}] must be a string literal"
                )
    return findings


def main() -> int:
    catalog = Path(__file__).resolve().parents[1] / "booktx" / "command_catalog.py"
    findings = validate_catalog_source(catalog)
    if findings:
        print("Command catalog source check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print(f"Command catalog source check passed: {catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the canonical booktx source and release quality gate.

The gate intentionally builds and exercises a wheel in a temporary virtual
environment. A source-tree import alone cannot prove that the console entry
point and package contents are usable after installation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class QualityGateFailure(RuntimeError):
    """A quality-gate stage failed."""


def _display_command(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def _python_tool(module: str, executable: str) -> list[str]:
    """Prefer the documented Python module command, with a local-tool fallback."""
    if importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module]
    candidates = [
        shutil.which(executable),
        str(Path(sys.executable).with_name(executable)),
    ]
    for located in candidates:
        if located is not None and Path(located).is_file():
            return [located]
    return [sys.executable, "-m", module]


def _run_stage(label: str, command: Sequence[str], *, cwd: Path = REPO_ROOT) -> None:
    rendered = _display_command(command)
    print(f"\n== {label} ==")
    print(f"$ {rendered}")
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        text=True,
        check=False,
    )
    if result.returncode:
        print(f"FAILED: {rendered}", file=sys.stderr)
        raise QualityGateFailure(f"{label} failed with exit code {result.returncode}")


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise QualityGateFailure(
            f"git {' '.join(args)} failed with exit code {result.returncode}"
        )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _python_in_venv(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _installed_booktx(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "booktx.exe"
    return venv_dir / "bin" / "booktx"


def _artifact_directory(path: Path | None, temporary_root: Path) -> Path:
    artifact_dir = path or temporary_root / "dist"
    artifact_dir = (
        artifact_dir if artifact_dir.is_absolute() else REPO_ROOT / artifact_dir
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    existing = tuple(artifact_dir.iterdir())
    if existing:
        names = ", ".join(item.name for item in existing)
        raise QualityGateFailure(
            "artifact directory must be empty before the build: "
            f"{artifact_dir} ({names})"
        )
    return artifact_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="fail unless the checked-out commit has no worktree changes",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="empty directory where the tested distributions are written",
    )
    parser.add_argument(
        "--evidence-file",
        type=Path,
        help="optional JSON path for the commit, wheel, and installation evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    commit_sha = _git_output("rev-parse", "HEAD")
    worktree_status = _git_output("status", "--porcelain")
    if args.require_clean and worktree_status:
        print(
            "FAILED: git status --porcelain (quality gate requires a clean worktree)",
            file=sys.stderr,
        )
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="booktx-quality-") as temporary_name:
            temporary_root = Path(temporary_name)
            artifact_dir = _artifact_directory(args.artifact_dir, temporary_root)

            _run_stage(
                "compile source",
                [sys.executable, "-m", "compileall", "-q", "booktx"],
            )
            _run_stage(
                "static command catalog check",
                [sys.executable, "scripts/check_command_catalog.py"],
            )
            _run_stage(
                "focused CLI/import tests",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_command_catalog.py",
                    "tests/test_import_health.py",
                    "tests/test_bootstrap.py",
                    "tests/test_agents_md.py",
                ],
            )
            _run_stage("full test suite", [sys.executable, "-m", "pytest", "-q"])
            _run_stage("Ruff", [*_python_tool("ruff", "ruff"), "check", "."])
            _run_stage("mypy", [*_python_tool("mypy", "mypy"), "booktx"])
            _run_stage(
                "build distributions",
                [sys.executable, "-m", "build", "--outdir", str(artifact_dir)],
            )

            wheels = sorted(artifact_dir.glob("*.whl"))
            if len(wheels) != 1:
                raise QualityGateFailure(
                    f"expected exactly one wheel in {artifact_dir}, found {len(wheels)}"
                )
            wheel = wheels[0]
            venv_dir = temporary_root / "wheel-venv"
            _run_stage(
                "create clean wheel environment",
                [sys.executable, "-m", "venv", str(venv_dir)],
            )
            venv_python = _python_in_venv(venv_dir)
            _run_stage(
                "install tested wheel",
                [str(venv_python), "-m", "pip", "install", str(wheel)],
            )
            installed_booktx = _installed_booktx(venv_dir)
            for command in (
                ("booktx --help", "--help"),
                ("booktx mode --help", "mode", "--help"),
                (
                    "booktx translate todo-doctor --help",
                    "translate",
                    "todo-doctor",
                    "--help",
                ),
            ):
                _run_stage(command[0], [str(installed_booktx), *command[1:]])

            evidence = {
                "result": "passed",
                "commit_sha": commit_sha,
                "python": platform.python_version(),
                "wheel_filename": wheel.name,
                "wheel_sha256": _sha256(wheel),
                "installation_target": str(venv_dir),
                "worktree_status": worktree_status,
            }
            print("\nQuality gate evidence:")
            print(json.dumps(evidence, indent=2, sort_keys=True))
            if args.evidence_file is not None:
                evidence_path = (
                    args.evidence_file
                    if args.evidence_file.is_absolute()
                    else REPO_ROOT / args.evidence_file
                )
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text(
                    json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                print(f"Evidence file: {evidence_path}")
    except QualityGateFailure as exc:
        print(f"Quality gate failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import compileall
import os
import subprocess
import sys
import sysconfig
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_compiles() -> None:
    assert compileall.compile_dir(Path("booktx"), quiet=1)


def test_cli_imports() -> None:
    from booktx.cli import app

    assert app.info.name == "booktx"


def test_basic_cli_import_does_not_import_source_analysis() -> None:
    code = textwrap.dedent(
        """
        import builtins
        import importlib

        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "booktx.source_analysis":
                raise AssertionError("source_analysis imported during basic CLI import")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import
        cli = importlib.import_module("booktx.cli")
        print(cli.app.info.name)
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(REPO_ROOT)
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == "booktx"


def _run_booktx_subprocess(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(REPO_ROOT)
    )
    booktx_name = "booktx.exe" if os.name == "nt" else "booktx"
    booktx_path = Path(sysconfig.get_path("scripts")) / booktx_name
    return subprocess.run(
        [str(booktx_path), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_import_subprocess() -> None:
    result = _run_booktx_subprocess("--help")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "booktx" in result.stdout


def test_root_help_subprocess() -> None:
    result = _run_booktx_subprocess("--help")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "booktx prepares books for agent-assisted translation" in result.stdout


def test_mode_help_subprocess() -> None:
    result = _run_booktx_subprocess("mode", "--help")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "mode" in result.stdout.lower()


def test_translate_todo_doctor_help_subprocess() -> None:
    result = _run_booktx_subprocess("translate", "todo-doctor", "--help")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Diagnose and safely supersede" in result.stdout


def test_cli_catalog_doctor_subprocess() -> None:
    result = _run_booktx_subprocess("doctor", "cli")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "CLI catalog: PASS" in result.stdout


def test_translate_migrate_store_help_subprocess() -> None:
    result = _run_booktx_subprocess("translate", "migrate-store", "--help")
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Inspect, migrate, verify, or roll back" in result.stdout
    assert "canonical translation" in result.stdout
    assert "store format" in result.stdout

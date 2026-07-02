from __future__ import annotations

import compileall
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_package_compiles() -> None:
    assert compileall.compile_dir(Path("booktx"), quiet=1)


def test_cli_imports() -> None:
    from booktx.cli import app

    assert app.info.name == "booktx"


def test_basic_cli_import_does_not_import_source_analysis() -> None:
    repo_root = Path(__file__).resolve().parents[1]
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
        f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(repo_root)
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == "booktx"

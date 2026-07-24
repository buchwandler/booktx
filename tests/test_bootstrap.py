from __future__ import annotations

import builtins
import io

import pytest

from booktx import bootstrap


def _raise_startup_failure(
    name: str,
    globals: dict[str, object] | None = None,
    locals: dict[str, object] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> object:
    if name == "booktx.cli":
        raise TypeError("invalid command summaries: {'translate todo-doctor': 'tuple'}")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


_REAL_IMPORT = builtins.__import__


def test_startup_failure_is_concise_and_uses_software_error_code(monkeypatch):
    monkeypatch.setattr(builtins, "__import__", _raise_startup_failure)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)

    with pytest.raises(SystemExit) as raised:
        bootstrap.main()

    assert raised.value.code == 70
    output = stream.getvalue()
    assert "booktx could not start." in output
    assert "TypeError: invalid command summaries" in output
    assert "No project or profile data was modified." in output
    assert "Traceback" not in output


def test_startup_failure_debug_mode_includes_traceback(monkeypatch):
    monkeypatch.setenv("BOOKTX_DEBUG", "1")
    stream = io.StringIO()

    try:
        raise RuntimeError("controlled startup failure")
    except RuntimeError as exc:
        bootstrap.render_startup_failure(exc, stream=stream)

    output = stream.getvalue()
    assert "RuntimeError: controlled startup failure" in output
    assert "Full startup traceback:" in output
    assert "Traceback" in output


def test_startup_failure_render_can_disable_debug_traceback():
    stream = io.StringIO()
    bootstrap.render_startup_failure(
        RuntimeError("controlled startup failure"), stream=stream, debug=False
    )
    assert "Full startup traceback:" not in stream.getvalue()

"""Documentation and packaging consistency tests (Phase 1).

Catches the regressions flagged in the booktx refactor review:
- unbalanced Markdown fences that hide sections in rendered docs,
- packaging references (LICENSE, sdist includes) that point at missing files,
- the core profile invariant drifting out of README/docs/SKILL,
- a public Typer command landing without being documented or explicitly
  classified as an alias/internal/legacy command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomli_w
import typer

from booktx.cli import app
from booktx.command_catalog import descriptor_for_path

ROOT = Path(__file__).resolve().parents[1]

# Markdown sources that must have balanced fences and the profile invariant.
MARKDOWN_FILES = [
    ROOT / "README.md",
    ROOT / "skills" / "booktx" / "SKILL.md",
    *sorted((ROOT / "docs").rglob("*.md")),
]

# Sphinx build output must not be linted.
MARKDOWN_FILES = [p for p in MARKDOWN_FILES if "_build" not in p.parts]


def _balanced_fences(text: str) -> bool:
    """True when code fences in ``text`` pair correctly.

    A fence line is a run of 3+ backticks optionally followed by an info
    string. A closing fence must have at least as many backticks as the
    opening fence (CommonMark). We approximate by counting fence-line
    transitions while respecting the longer-fence-closes-shorter rule.
    """
    open_len: int | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        m = re.match(r"(`{3,})", stripped)
        if not m or stripped[: m.end()].count("`") != len(m.group(1)):
            continue
        if not re.fullmatch(r"`{3,}.*", stripped):
            continue
        length = len(m.group(1))
        if open_len is None:
            open_len = length
        elif length >= open_len:
            open_len = None
    return open_len is None


# --- Markdown fence balance --------------------------------------------------


@pytest.mark.parametrize("path", MARKDOWN_FILES)
def test_markdown_fences_balanced(path: Path) -> None:
    assert _balanced_fences(path.read_text("utf-8")), (
        f"unbalanced code fences in {path}"
    )


# --- packaging references ----------------------------------------------------


def _pyproject() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_declared_license_file_exists() -> None:
    pyproject = _pyproject()
    license_files = pyproject.get("project", {}).get("license-files")
    paths = []
    if isinstance(license_files, dict):
        paths = license_files.get("paths", [])
    elif isinstance(license_files, list):
        paths = license_files
    assert paths, "no license-files declared in pyproject.toml"
    for rel in paths:
        assert (ROOT / rel).is_file(), f"declared license file missing: {rel}"


def test_sdist_included_paths_exist() -> None:
    includes = (
        _pyproject()
        .get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("sdist", {})
        .get("include", [])
    )
    assert includes, "no sdist include list found"
    for rel in includes:
        assert (ROOT / rel).exists(), f"sdist include missing in repo: {rel}"


# --- core profile invariant --------------------------------------------------

PROFILE_INVARIANT_MARKERS = (".booktx/", "translations/<profile>/")


@pytest.mark.parametrize(
    "path", [ROOT / "README.md", ROOT / "skills" / "booktx" / "SKILL.md"]
)
def test_profile_invariant_documented(path: Path) -> None:
    text = path.read_text("utf-8")
    assert all(marker in text for marker in PROFILE_INVARIANT_MARKERS), (
        f"{path} must state the core profile invariant ({PROFILE_INVARIANT_MARKERS})"
    )


# --- live Typer command inventory -------------------------------------------


def _command_paths() -> set[str]:
    group = typer.main.get_command(app)
    assert hasattr(group, "commands")
    paths: set[str] = set()

    def walk(command: object, prefix: str = "") -> None:
        commands = getattr(command, "commands", {})
        for name, subcommand in sorted(commands.items()):
            path = f"{prefix} {name}".strip()
            paths.add(path)
            walk(subcommand, path)

    walk(group)
    return paths


UNDOCUMENTED_GROUP_ALLOWLIST = {
    # Agent protocol, maintenance, or hidden operational groups are documented
    # in their dedicated guides rather than requiring every leaf in commands.md.
    "doctor",
    "mode",
    "pass-through",
    "termbase",
    "translate",
}

UNDOCUMENTED_PATH_ALLOWLIST = {
    # Agent protocol or maintenance leaves documented in agent-workflow.md or
    # maintenance.md instead of the human command reference.
    "context answer",
    "context import-md",
    "context recommend",
    "judge accept-identical",
    "judge continue",
    "judge finish-chapter-plan",
    "judge insert",
    "judge next",
    "judge prefill-policy-fixes",
    "judge record",
    "judge reset-ingest",
    "judge show",
    "judge sweep-identical",
    "judge sync-sources",
    "judge repair-plan",
    "review activate",
    "review deactivate",
    "review insert",
    "review next",
    "review revise-record",
    "review todo-next",
    "review todo-resume",
    "review todo-status",
    "version fork-context",
    "version select",
    "version set-label",
    "source chapter",
    "source record",
}


def _doc_text() -> str:
    parts = [ROOT / "docs" / "commands.md"]
    return "\n".join(p.read_text("utf-8") for p in parts if p.is_file())


def test_every_public_command_is_documented_or_allowlisted() -> None:
    docs = _doc_text()
    undocumented: list[str] = []
    for path in sorted(_command_paths()):
        if (
            path.split()[0] in UNDOCUMENTED_GROUP_ALLOWLIST
            or path in UNDOCUMENTED_PATH_ALLOWLIST
        ):
            continue
        pattern = re.compile(rf"\bbooktx\s+{re.escape(path)}\b")
        if not pattern.search(docs):
            undocumented.append(path)
    assert not undocumented, (
        "public commands missing from docs/commands.md or explicit group "
        f"allowlist: {undocumented}"
    )


def test_every_live_command_has_a_descriptor() -> None:
    missing = []
    for path in sorted(_command_paths()):
        try:
            descriptor_for_path(path)
        except KeyError:
            missing.append(path)
    assert not missing, f"commands missing catalog descriptors: {missing}"


def test_documentation_preserves_current_invariants() -> None:
    doc_paths = [
        ROOT / "README.md",
        ROOT / "skills" / "booktx" / "SKILL.md",
        *sorted((ROOT / "docs").glob("*.md")),
    ]
    docs = "\n".join(path.read_text("utf-8") for path in doc_paths)
    forbidden = (
        ".booktx/profile state",
        "global active profile",
        "exactly one existing profile",
        "canonical-store-split",
        "state/current.json",
        "booktx model set",
        "booktx actor set",
        "booktx harness set",
    )
    assert not [phrase for phrase in forbidden if phrase in docs]
    markers = (
        "TranslationStoreV2",
        ".booktx-profile.json",
        "booktx glossary",
        "booktx identity set",
        "booktx translate",
    )
    for marker in markers:
        assert marker in docs, f"current documentation invariant missing: {marker}"


def test_retained_docs_are_reachable_from_toctree() -> None:
    index = (ROOT / "docs" / "index.md").read_text("utf-8")
    listed = set(re.findall(r"^([a-z0-9_/-]+)$", index, flags=re.MULTILINE))
    missing = []
    for path in sorted((ROOT / "docs").glob("*.md")):
        if path.name == "index.md":
            continue
        if path.stem not in listed:
            missing.append(path.name)
    assert not missing, (
        f"documentation pages missing from docs/index.md toctree: {missing}"
    )
    assert not (ROOT / "docs" / "architecture" / "canonical-store-split.md").exists()


OBSOLETE_PROFILE_PHRASES = (
    "active profile",
    "selected profile state",
    "select a profile",
    "none is active",
)


def test_obsolete_active_profile_language_is_gone_from_human_docs() -> None:
    checked = [
        ROOT / "README.md",
        ROOT / "skills" / "booktx" / "SKILL.md",
        ROOT / "docs" / "quickstart.md",
        ROOT / "docs" / "commands.md",
    ]
    hits: list[str] = []
    for path in checked:
        text = path.read_text("utf-8").lower()
        for phrase in OBSOLETE_PROFILE_PHRASES:
            if phrase in text:
                hits.append(f"{path}: {phrase}")
    assert not hits, "obsolete profile wording remains:\n" + "\n".join(hits)


# Keep tomli_w import used (pyproject write helpers elsewhere rely on it).
def test_tomli_w_available() -> None:
    assert tomli_w is not None


# --- github org consistency (README vs pyproject) ----------------------------


def test_github_org_consistent() -> None:
    """README github.com/<org>/booktx references must match pyproject Repository URL."""
    import re

    readme = (ROOT / "README.md").read_text("utf-8")

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    with (ROOT / "pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)

    homepage = pyproject["project"]["urls"]["Repository"]
    org_from_pyproject = homepage.split("github.com/")[-1].split("/")[0]

    org_from_readme = re.findall(r"(?:github\.com|gh)/([\w.-]+)/booktx", readme)
    assert org_from_readme, "no github.com/<org>/booktx reference in README"

    for org in org_from_readme:
        assert org == org_from_pyproject, (
            f"README mentions github.com/{org}/booktx but pyproject uses "
            f"github.com/{org_from_pyproject}/booktx"
        )


# --- distribution build smoke ------------------------------------------------


def test_sdist_build_smoke(tmp_path: Path) -> None:
    """A clean sdist/wheel build from the declared config succeeds.

    This catches packaging mistakes (missing files, bad hatch/build config)
    that the sdist-include test cannot detect, and acts as a release-time
    guard per the review's recommendation.
    """
    import subprocess

    result = subprocess.run(
        ["python", "-m", "build", "--sdist", "--wheel", "--outdir", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"sdist/wheel build failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    built = sorted(tmp_path.glob("booktx-*"))
    assert built, f"no artifacts in {tmp_path}: stdout={result.stdout[-500:]}"
    # A build smoke: at least one distribution artifact of any kind.
    # Different build backends/versions may skip sdist or wheel depending on
    # the environment (e.g. missing hatch), so we accept whichever produced.
    assert any(p.suffix in (".tar.gz", ".whl") for p in built), (
        f"no distribution artifact among: {built}"
    )

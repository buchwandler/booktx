"""Project configuration and path resolution for legacy and profile layouts.

booktx now supports two project shapes.

Legacy single-layout projects::

    book/
      source/
      .booktx/
        config.toml
        manifest.json
        names.json
        chunks/
        context.json
        context.md
        identity.json
        translation-store.json
        translation-version-ledger.json
        tasks/
        ingest/
        translated/
        reports/
        output/

Profile-layout projects::

    book/
      source/
      .booktx/
        source-config.toml
        source-manifest.json
        names.json
        chapter-map.json
        chunks/
      translations/
        <profile>/
          config.toml
          identity.json
          context.json
          context.md
          translation-store.json
          translation-version-ledger.json
          tasks/
          ingest/
          translated/
          reports/
          output/

The shared ``.booktx/`` tree is source-derived state only. Mutable translation
state lives under the explicitly resolved profile.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import tomli_w

from booktx.errors import BooktxError, _err
from booktx.path_ids import safe_artifact_id

try:
    import tomllib  # type: ignore[import-not-found]  # Python 3.11+ stdlib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found]

from booktx.epub_manifest import sha256_path
from booktx.models import (
    ContextSyncLedger,
    JudgeTask,
    Manifest,
    NamesFile,
    ProfileConfig,
    ProfileIdentityConfig,
    ProfileRootMarker,
    ProjectConfig,
    SourceConfig,
    TranslationIdentity,
    TranslationReviewTask,
    TranslationSelectionLedger,
    TranslationStore,
    TranslationStoreV2,
    TranslationTask,
    TranslationVersionLedger,
)

__all__ = [
    "SUPPORTED_SOURCE_SUFFIXES",
    "BooktxError",
    "_err",
    "Project",
    "detect_format",
    "source_config_path",
    "translations_dir",
    "profile_dir",
    "profile_config_path",
    "profile_root_marker_path",
    "profile_source_cache_dir",
    "list_profiles",
    "load_profile_config",
    "load_profile_root_marker",
    "write_profile_config",
    "write_profile_root_marker",
    "resolve_profile_name",
    "load_project",
    "load_source_project",
    "load_profile_project",
    "init_source_project",
    "init_project",
    "create_profile",
    "migrate_current_project",
    "write_manifest",
    "load_manifest",
    "write_names",
    "load_names",
    "protected_terms_sha256",
    "project_source_sha256",
    "project_source_id",
    "project_source_id_or_unavailable",
    "current_source_sha256",
    "extracted_source_sha256",
    "translation_store_path",
    "translation_version_ledger_path",
    "identity_path",
    "load_translation_store",
    "load_translation_version_ledger",
    "load_identity",
    "write_translation_store",
    "write_translation_version_ledger",
    "write_identity",
    "translation_task_dir",
    "translation_task_path",
    "translation_task_source_block_path",
    "translation_ingest_dir",
    "translation_ingest_path",
    "translation_ingest_block_path",
    "translation_todo_dir",
    "translation_todo_json_path",
    "translation_todo_markdown_path",
    "review_todo_dir",
    "review_todo_json_path",
    "review_todo_markdown_path",
    "load_translation_task",
    "write_translation_task",
    "translation_review_dir",
    "translation_review_task_path",
    "translation_review_source_block_path",
    "translation_review_ingest_block_path",
    "translation_source_index_path",
    "translation_target_index_path",
    "translation_source_target_index_path",
    "source_analysis_path",
    "source_analysis_markdown_path",
    "source_analysis_decisions_path",
    "profile_source_analysis_path",
    "profile_source_analysis_markdown_path",
    "context_sync_ledger_path",
    "load_context_sync_ledger",
    "write_context_sync_ledger",
    "translation_selection_ledger_path",
    "load_translation_selection_ledger",
    "write_translation_selection_ledger",
    "canonical_language_key",
    "termbase_language_keys",
    "global_termbase_dir",
    "global_termbase_path",
    "project_termbase_path",
    "profile_termbase_path",
    "profile_termbase_snapshot_path",
    "judge_task_dir",
    "judge_task_path",
    "judge_task_source_block_path",
    "judge_ingest_dir",
    "judge_ingest_block_path",
    "judge_ingest_json_path",
    "load_judge_task",
    "write_judge_task",
    "load_translation_review_task",
    "write_translation_review_task",
    "find_source_file",
    "project_storage_root",
    "stored_path",
    "resolve_stored_path",
]

SUPPORTED_SOURCE_SUFFIXES: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".epub": "epub",
}

DEFAULT_NAMES_JSON: dict[str, Any] = {"protected_terms": []}
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PROFILE_ROOT_MARKER_FILENAME = ".booktx-profile.json"
LANGUAGE_KEY_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})*$")


# ``BooktxError`` / ``_err`` are imported from :mod:`booktx.errors` at the
# top of this module (see import block) and re-exported via ``__all__`` so
# existing ``from booktx.config import BooktxError`` keeps working.


@dataclass(slots=True)
class Project:
    """Resolved project paths for either the legacy or profile layout."""

    root: Path
    source_dir: Path
    booktx_dir: Path
    translations_dir: Path
    source_config_path: Path
    config_path: Path
    manifest_path: Path
    names_path: Path
    chunks_dir: Path
    chapter_map_path: Path
    profile: str | None
    profile_dir: Path | None
    profile_config_path: Path | None
    context_json_path: Path | None
    context_md_path: Path | None
    identity_json_path: Path | None
    store_path: Path | None
    ledger_path: Path | None
    translated_dir: Path | None
    tasks_dir: Path | None
    ingest_dir: Path | None
    reports_dir: Path | None
    output_dir: Path | None
    source_config: SourceConfig
    profile_config: ProfileConfig | None
    config: ProjectConfig
    layout_version: Literal["legacy", "profiles"]

    @property
    def source_path(self) -> Path:
        return self.source_dir / self.config.source_file

    def chunks(self) -> list[Path]:
        return sorted(self.chunks_dir.glob("*.json"))

    def translated(self) -> list[Path]:
        if self.translated_dir is None:
            return []
        return sorted(self.translated_dir.glob("*.json"))

    def chunk_ids(self) -> list[str]:
        return [path.stem for path in self.chunks()]

    def translated_ids(self) -> list[str]:
        return [path.stem for path in self.translated()]


def _root_path(root_or_project: Project | Path | str) -> Path:
    if isinstance(root_or_project, Project):
        return root_or_project.root
    return Path(root_or_project).expanduser().resolve()


def _booktx_dir(root_or_project: Project | Path | str) -> Path:
    return _root_path(root_or_project) / ".booktx"


def _legacy_config_path(root_or_project: Project | Path | str) -> Path:
    return _booktx_dir(root_or_project) / "config.toml"


def source_config_path(root_or_project: Project | Path | str) -> Path:
    return _booktx_dir(root_or_project) / "source-config.toml"


def _legacy_manifest_path(root_or_project: Project | Path | str) -> Path:
    return _booktx_dir(root_or_project) / "manifest.json"


def _source_manifest_path(root_or_project: Project | Path | str) -> Path:
    return _booktx_dir(root_or_project) / "source-manifest.json"


def translations_dir(root_or_project: Project | Path | str) -> Path:
    return _root_path(root_or_project) / "translations"


def profile_dir(root_or_project: Project | Path | str, profile_name: str) -> Path:
    return translations_dir(root_or_project) / profile_name


def profile_config_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "config.toml"


def profile_root_marker_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / PROFILE_ROOT_MARKER_FILENAME


def profile_source_cache_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "source-cache"


def _profile_context_json_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "context.json"


def _profile_context_md_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "context.md"


def _profile_identity_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "identity.json"


def _profile_store_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "translation-store.json"


def _profile_ledger_path(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return (
        profile_dir(root_or_project, profile_name) / "translation-version-ledger.json"
    )


def _profile_translated_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "translated"


def _profile_tasks_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "tasks"


def _profile_ingest_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "ingest"


def _profile_reports_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "reports"


def _profile_output_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "output"


def _profile_judge_tasks_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "judge-tasks"


def _profile_judge_ingest_dir(
    root_or_project: Project | Path | str, profile_name: str
) -> Path:
    return profile_dir(root_or_project, profile_name) / "judge-ingest"


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise _err("invalid_toml", f"{path} did not contain a TOML object")
    return data


def _write_toml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(payload).encode("utf-8"))


def _read_legacy_config(path: Path) -> ProjectConfig:
    return ProjectConfig.model_validate(_read_toml(path))


def _write_legacy_config(path: Path, cfg: ProjectConfig) -> None:
    _write_toml(path, cfg.model_dump(mode="json", exclude_none=True))


def _read_source_config(path: Path) -> SourceConfig:
    return SourceConfig.model_validate(_read_toml(path))


def _write_source_config(path: Path, cfg: SourceConfig) -> None:
    _write_toml(path, cfg.model_dump(mode="json", exclude_none=True))


def load_profile_config(
    project_or_root: Project | Path | str, profile_name: str
) -> ProfileConfig:
    validate_profile_name(profile_name)
    path = profile_config_path(project_or_root, profile_name)
    if not path.is_file():
        raise _err(
            "profile_not_found", f"translation profile not found: {profile_name}"
        )
    return ProfileConfig.model_validate(_read_toml(path))


def write_profile_config(
    project_or_root: Project | Path | str, cfg: ProfileConfig
) -> None:
    validate_profile_name(cfg.profile)
    _write_toml(
        profile_config_path(project_or_root, cfg.profile),
        cfg.model_dump(mode="json", exclude_none=True),
    )


def load_profile_root_marker(profile_root: Path | str) -> ProfileRootMarker:
    root = Path(profile_root).expanduser().resolve()
    path = root / PROFILE_ROOT_MARKER_FILENAME
    if not path.is_file():
        raise _err(
            "profile_root_marker_missing",
            f"profile root marker is missing: {path.name}",
        )
    try:
        return ProfileRootMarker.model_validate_json(path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _err(
            "invalid_profile_root_marker", f"profile root marker is invalid: {exc}"
        ) from exc


def write_profile_root_marker(
    project_or_root: Project | Path | str,
    profile_name: str,
    *,
    profile_config: ProfileConfig | None = None,
) -> ProfileRootMarker:
    validate_profile_name(profile_name)
    source_project = load_source_project(_root_path(project_or_root))
    if source_project.layout_version != "profiles":
        raise _err(
            "legacy_project_required",
            "project uses the legacy single-layout format; profile-root markers are only available for translation profiles",
        )
    cfg = profile_config or load_profile_config(source_project, profile_name)
    marker = ProfileRootMarker(
        profile=profile_name,
        source_id=project_source_id_or_unavailable(source_project),
        target_language=cfg.target_language,
        target_locale=cfg.target_locale or cfg.target_language,
    )
    from booktx.io_utils import write_json_text_atomic

    path = profile_root_marker_path(source_project, profile_name)
    # Atomic write so an interrupted preparation command never leaves a
    # half-written marker behind; booktx AGENTS.md preparation relies on this.
    write_json_text_atomic(path, marker.model_dump_json(indent=2, by_alias=True))
    return marker


def list_profiles(project_or_root: Project | Path | str) -> list[str]:
    root = translations_dir(project_or_root)
    if not root.is_dir():
        return []
    names: list[str] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        if not (path / "config.toml").is_file():
            continue
        names.append(path.name)
    return names


def detect_format(filename: str | Path) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SOURCE_SUFFIXES:
        raise _err(
            "unsupported_format",
            f"Unsupported source format '{suffix or '<none>'}'. booktx supports only .md and .epub.",
        )
    return SUPPORTED_SOURCE_SUFFIXES[suffix]


def validate_profile_name(profile_name: str) -> str:
    if not profile_name:
        raise _err("invalid_profile_name", "translation profile name must not be empty")
    path = Path(profile_name)
    if (
        path.is_absolute()
        or path.name != profile_name
        or "/" in profile_name
        or "\\" in profile_name
    ):
        raise _err(
            "invalid_profile_name",
            f"invalid translation profile name: {profile_name!r}",
        )
    if not PROFILE_NAME_RE.fullmatch(profile_name):
        raise _err(
            "invalid_profile_name",
            "profile names may contain only letters, numbers, '.', '_' and '-'",
        )
    return profile_name


def canonical_language_key(language: str) -> str:
    """Return a canonical language shard key such as ``de`` or ``de-DE``."""
    raw = language.strip()
    if (
        not raw
        or "/" in raw
        or "\\" in raw
        or ".." in raw
        or raw.startswith(".")
        or raw.endswith(".json")
        or not LANGUAGE_KEY_RE.fullmatch(raw)
    ):
        raise _err("invalid_language_key", f"invalid language key: {language!r}")
    parts = raw.split("-")
    canonical_parts = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            canonical_parts.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            canonical_parts.append(part.title())
        else:
            canonical_parts.append(part)
    return "-".join(canonical_parts)


def termbase_language_keys(project: Project, language: str | None = None) -> list[str]:
    """Resolve the base-plus-locale language-key sequence for termbase reads."""
    if language is not None:
        key = canonical_language_key(language)
        base = key.split("-", 1)[0]
        return [base] if key == base else [base, key]
    if project.profile_config is None:
        if not project.config.target_language:
            raise _err(
                "termbase_language_required",
                "--language is required when no profile target language is available",
            )
        base = canonical_language_key(project.config.target_language)
        locale_raw = (project.config.target_locale or "").strip()
    else:
        base = canonical_language_key(project.profile_config.target_language)
        locale_raw = (project.profile_config.target_locale or "").strip()
    if not locale_raw:
        return [base]
    locale = canonical_language_key(locale_raw)
    return [base] if locale == base else [base, locale]


def global_termbase_dir() -> Path:
    """Directory containing user-global translation termbase shards."""
    override = os.environ.get("BOOKTX_TERMBASE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "booktx" / "translation-termbase").resolve()


def global_termbase_path(language_key: str) -> Path:
    """Path to one user-global translation termbase shard."""
    key = canonical_language_key(language_key)
    return global_termbase_dir() / f"{key}.json"


def project_termbase_path(project: Project, language_key: str) -> Path:
    """Path to one project/series termbase shard."""
    key = canonical_language_key(language_key)
    return project.booktx_dir / "termbase" / f"{key}.json"


def profile_termbase_path(project: Project, language_key: str) -> Path:
    """Path to one profile-local termbase override shard."""
    _require_profile_paths(project, "profile termbase access")
    key = canonical_language_key(language_key)
    return (
        profile_dir(project.root, project.profile or "")
        / "termbase-overrides"
        / f"{key}.json"
    )


def profile_termbase_snapshot_path(project: Project, language_key: str) -> Path:
    """Path to one frozen termbase snapshot shard for profile-root isolation."""
    _require_profile_paths(project, "profile termbase snapshot access")
    key = canonical_language_key(language_key)
    return (
        profile_dir(project.root, project.profile or "")
        / "termbase-snapshot"
        / f"{key}.json"
    )


SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{1,128}$")


def validate_snapshot_id(snapshot_id: str) -> str:
    """Validate a judge-sources snapshot generation digest before it enters a path.

    Snapshot ids are lowercase hexadecimal digests (any length up to 128 hex
    chars) so they are always path-safe and deterministic. They must never
    contain ``..``, separators, or upper-case characters.
    """
    if not snapshot_id or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise _err(
            "invalid_snapshot_id",
            "snapshot id must be a lowercase hexadecimal digest",
        )
    return snapshot_id


def _is_legacy_layout(root: Path) -> bool:
    return (
        _legacy_config_path(root).is_file() and not source_config_path(root).is_file()
    )


def _is_profile_layout(root: Path) -> bool:
    return source_config_path(root).is_file()


def _base_profile_project(root: Path, source_cfg: SourceConfig) -> Project:
    effective = ProjectConfig(
        source_language=source_cfg.source_language,
        source_file=source_cfg.source_file,
        format=source_cfg.format,
        chunk_size=source_cfg.chunk_size,
    )
    return Project(
        root=root,
        source_dir=root / "source",
        booktx_dir=_booktx_dir(root),
        translations_dir=translations_dir(root),
        source_config_path=source_config_path(root),
        config_path=source_config_path(root),
        manifest_path=_source_manifest_path(root),
        names_path=_booktx_dir(root) / "names.json",
        chunks_dir=_booktx_dir(root) / "chunks",
        chapter_map_path=_booktx_dir(root) / "chapter-map.json",
        profile=None,
        profile_dir=None,
        profile_config_path=None,
        context_json_path=None,
        context_md_path=None,
        identity_json_path=None,
        store_path=None,
        ledger_path=None,
        translated_dir=None,
        tasks_dir=None,
        ingest_dir=None,
        reports_dir=None,
        output_dir=None,
        source_config=source_cfg,
        profile_config=None,
        config=effective,
        layout_version="profiles",
    )


def _with_profile(
    base: Project, profile_name: str, profile_cfg: ProfileConfig
) -> Project:
    effective = ProjectConfig(
        source_language=base.source_config.source_language,
        target_language=profile_cfg.target_language,
        target_locale=profile_cfg.target_locale or profile_cfg.target_language,
        output_filename=profile_cfg.output_filename,
        source_file=base.source_config.source_file,
        format=base.source_config.format,
        chunk_size=base.source_config.chunk_size,
    )
    profile_root = profile_dir(base, profile_name)
    return Project(
        root=base.root,
        source_dir=base.source_dir,
        booktx_dir=base.booktx_dir,
        translations_dir=base.translations_dir,
        source_config_path=base.source_config_path,
        config_path=base.config_path,
        manifest_path=base.manifest_path,
        names_path=base.names_path,
        chunks_dir=base.chunks_dir,
        chapter_map_path=base.chapter_map_path,
        profile=profile_name,
        profile_dir=profile_root,
        profile_config_path=profile_config_path(base, profile_name),
        context_json_path=_profile_context_json_path(base, profile_name),
        context_md_path=_profile_context_md_path(base, profile_name),
        identity_json_path=_profile_identity_path(base, profile_name),
        store_path=_profile_store_path(base, profile_name),
        ledger_path=_profile_ledger_path(base, profile_name),
        translated_dir=_profile_translated_dir(base, profile_name),
        tasks_dir=_profile_tasks_dir(base, profile_name),
        ingest_dir=_profile_ingest_dir(base, profile_name),
        reports_dir=_profile_reports_dir(base, profile_name),
        output_dir=_profile_output_dir(base, profile_name),
        source_config=base.source_config,
        profile_config=profile_cfg,
        config=effective,
        layout_version="profiles",
    )


def project_storage_root(project: Project) -> Path:
    """Return the root used for persisted profile-local path strings."""
    if project.layout_version == "profiles" and project.profile_dir is not None:
        return project.profile_dir
    return project.root


def stored_path(project: Project, path: Path) -> str:
    """Persist a path relative to the resolved profile when available."""
    return path.relative_to(project_storage_root(project)).as_posix()


def resolve_stored_path(project: Project, stored_rel_path: str) -> Path:
    """Resolve a persisted path against legacy root-relative or profile-relative roots."""
    rel_path = Path(stored_rel_path)
    legacy_candidate = project.root / rel_path
    if legacy_candidate.exists():
        return legacy_candidate
    return project_storage_root(project) / rel_path


def resolve_profile_name(
    project: Project,
    explicit_profile: str | None,
    *,
    require_profile: bool,
    operation: str = "command",
) -> str | None:
    if project.layout_version == "legacy":
        if explicit_profile:
            raise _err(
                "profile_not_found",
                f"project does not use translation profiles; remove --profile {explicit_profile}",
            )
        return None

    profiles = list_profiles(project)
    if explicit_profile:
        validate_profile_name(explicit_profile)
        if explicit_profile not in profiles:
            raise _err(
                "profile_not_found",
                f"translation profile not found: {explicit_profile}",
            )
        return explicit_profile

    if require_profile:
        if profiles:
            available = ", ".join(profiles)
            suffix = f" Available profiles: {available}." if available else ""
            raise _err(
                "profile_required",
                "translation profile required; pass --profile PROFILE." + suffix,
            )
        raise _err(
            "no_profiles",
            "no translation profile exists; run "
            "`booktx profile create PROJECT_DIR PROFILE --target LANG`",
        )
    _ = operation
    return None


def load_source_project(root: Path | str) -> Project:
    r = Path(root).expanduser().resolve()
    if _is_profile_layout(r):
        return _base_profile_project(r, _read_source_config(source_config_path(r)))
    if _is_legacy_layout(r):
        return load_project(r)
    raise _err(
        "not_a_project",
        f"{r} is not a booktx project: missing {_legacy_config_path(r)} or {source_config_path(r)}.",
    )


def load_profile_project(root: Path | str, profile: str) -> Project:
    return load_project(root, profile=profile, require_profile=True)


def load_project(
    root: Path | str,
    *,
    profile: str | None = None,
    require_profile: bool = False,
) -> Project:
    r = Path(root).expanduser().resolve()

    if _is_profile_layout(r):
        base = _base_profile_project(r, _read_source_config(source_config_path(r)))
        selected_profile = resolve_profile_name(
            base,
            profile,
            require_profile=require_profile,
        )
        if selected_profile is None:
            return base
        return _with_profile(
            base, selected_profile, load_profile_config(base, selected_profile)
        )

    if _is_legacy_layout(r):
        cfg = _read_legacy_config(_legacy_config_path(r))
        source_cfg = SourceConfig(
            source_language=cfg.source_language,
            source_file=cfg.source_file,
            format=cfg.format,
            chunk_size=cfg.chunk_size,
        )
        return Project(
            root=r,
            source_dir=r / "source",
            booktx_dir=_booktx_dir(r),
            translations_dir=translations_dir(r),
            source_config_path=_legacy_config_path(r),
            config_path=_legacy_config_path(r),
            manifest_path=_legacy_manifest_path(r),
            names_path=_booktx_dir(r) / "names.json",
            chunks_dir=_booktx_dir(r) / "chunks",
            chapter_map_path=_booktx_dir(r) / "chapter-map.json",
            profile=None,
            profile_dir=None,
            profile_config_path=None,
            context_json_path=_booktx_dir(r) / "context.json",
            context_md_path=_booktx_dir(r) / "context.md",
            identity_json_path=_booktx_dir(r) / "identity.json",
            store_path=_booktx_dir(r) / "translation-store.json",
            ledger_path=_booktx_dir(r) / "translation-version-ledger.json",
            translated_dir=_booktx_dir(r) / "translated",
            tasks_dir=_booktx_dir(r) / "tasks",
            ingest_dir=_booktx_dir(r) / "ingest",
            reports_dir=_booktx_dir(r) / "reports",
            output_dir=r / "output",
            source_config=source_cfg,
            profile_config=None,
            config=cfg,
            layout_version="legacy",
        )

    raise _err(
        "not_a_project",
        f"{r} is not a booktx project: missing {_legacy_config_path(r)} or {source_config_path(r)}.",
    )


def init_source_project(
    target: Path,
    *,
    source_language: str = "en",
    source_file: Path | str | None = None,
    chunk_size: int = 50,
) -> Project:
    root = Path(target).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise _err("not_a_directory", f"{root} exists and is not a directory.")
    root.mkdir(parents=True, exist_ok=True)

    source_dir = root / "source"
    booktx_dir = _booktx_dir(root)
    chunks_dir = booktx_dir / "chunks"
    translations_root = translations_dir(root)
    for path in (source_dir, booktx_dir, chunks_dir, translations_root):
        path.mkdir(parents=True, exist_ok=True)

    rel_source_name = ""
    fmt = "markdown"
    if source_file is not None:
        src = Path(source_file).expanduser().resolve()
        if not src.is_file():
            raise _err("source_not_found", f"Source file not found: {src}")
        fmt = detect_format(src.name)
        dest = source_dir / src.name
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
        rel_source_name = src.name

    cfg = SourceConfig(
        source_language=source_language,
        source_file=rel_source_name,
        format=fmt,
        chunk_size=chunk_size,
    )
    _write_source_config(source_config_path(root), cfg)

    from booktx.io_utils import write_json_text_atomic

    write_json_text_atomic(
        booktx_dir / "names.json",
        json.dumps(DEFAULT_NAMES_JSON, indent=2, ensure_ascii=False),
    )
    return load_source_project(root)


def _default_profile_name(target_language: str) -> str:
    return f"{target_language}_default"


def _default_output_filename(source_cfg: SourceConfig, target_language: str) -> str:
    source_name = source_cfg.source_file or "book"
    stem = Path(source_name).stem or "book"
    suffix = ".epub" if source_cfg.format == "epub" else ".md"
    return f"{stem}.{target_language}{suffix}"


def _validate_output_filename(
    *,
    output_filename: str,
    source_cfg: SourceConfig,
    target_language: str,
) -> None:
    suffix = ".epub" if source_cfg.format == "epub" else ".md"
    expected_fragment = f".{target_language}{suffix}"
    if not output_filename.endswith(suffix) or expected_fragment not in output_filename:
        raise _err(
            "output_target_mismatch",
            f"output filename {output_filename} does not match target language {target_language}; "
            f"expected a filename like {_default_output_filename(source_cfg, target_language)}",
        )


def _materialize_source_analysis_snapshot(
    source_project: Project, profile_name: str
) -> None:
    """Copy current canonical source analysis into a newly created profile."""
    canonical_path = source_analysis_path(source_project)
    if not canonical_path.is_file():
        return

    from booktx.io_utils import (
        utc_timestamp,
        write_json_text_atomic,
        write_text_atomic,
    )
    from booktx.source_analysis import (
        build_snapshot,
        read_canonical_report,
        render_report_markdown,
    )

    report = read_canonical_report(source_project)
    if report is None:
        return
    profile_project = load_profile_project(source_project.root, profile_name)
    snapshot = build_snapshot(
        report,
        profile=profile_name,
        generated_at=utc_timestamp(),
    )
    write_json_text_atomic(
        profile_source_analysis_path(profile_project),
        snapshot.model_dump_json(by_alias=True),
    )
    write_text_atomic(
        profile_source_analysis_markdown_path(profile_project),
        render_report_markdown(report),
    )


def create_profile(
    root: Path | str,
    profile_name: str,
    *,
    target_language: str,
    target_locale: str | None = None,
    actor: str | None = None,
    harness: str | None = None,
    model: str | None = None,
    output_filename: str | None = None,
    kind: Literal["translation", "pass-through", "selection"] = "translation",
) -> Project:
    validate_profile_name(profile_name)
    source_project = load_source_project(root)
    if source_project.layout_version == "legacy":
        raise _err(
            "legacy_project_required",
            "project uses the legacy single-layout format; run `booktx profile migrate-current` first",
        )
    existing_dir = profile_dir(source_project, profile_name)
    if existing_dir.exists() and any(existing_dir.iterdir()):
        raise _err(
            "profile_already_exists",
            f"translation profile already exists: {profile_name}",
        )
    existing_cfg = profile_config_path(source_project, profile_name)
    if existing_cfg.is_file():
        raise _err(
            "profile_already_exists",
            f"translation profile already exists: {profile_name}",
        )

    final_output_filename = output_filename or _default_output_filename(
        source_project.source_config, target_language
    )
    _validate_output_filename(
        output_filename=final_output_filename,
        source_cfg=source_project.source_config,
        target_language=target_language,
    )
    identity_cfg = ProfileIdentityConfig(
        actor=actor or "user:unknown",
        harness=harness or "booktx",
        model=model or "human",
    )
    cfg = ProfileConfig(
        profile=profile_name,
        kind=kind,
        source_language=source_project.source_config.source_language,
        target_language=target_language,
        target_locale=target_locale or target_language,
        output_filename=final_output_filename,
        identity=identity_cfg,
    )
    profile_root = profile_dir(source_project, profile_name)
    for path in (
        profile_root,
        _profile_tasks_dir(source_project, profile_name),
        _profile_ingest_dir(source_project, profile_name),
        _profile_translated_dir(source_project, profile_name),
        _profile_reports_dir(source_project, profile_name),
        _profile_output_dir(source_project, profile_name),
    ):
        path.mkdir(parents=True, exist_ok=True)
    write_profile_config(source_project, cfg)
    write_identity(
        _with_profile(source_project, profile_name, cfg),
        TranslationIdentity(
            actor=identity_cfg.actor,
            harness=identity_cfg.harness,
            model=identity_cfg.model,
        ),
    )
    write_profile_root_marker(source_project, profile_name, profile_config=cfg)
    _materialize_source_analysis_snapshot(source_project, profile_name)
    return load_profile_project(source_project.root, profile_name)


def init_project(
    target: Path,
    *,
    target_language: str = "",
    profile_name: str | None = None,
    source_language: str = "en",
    source_file: Path | str | None = None,
    chunk_size: int = 50,
) -> Project:
    project = init_source_project(
        target,
        source_language=source_language,
        source_file=source_file,
        chunk_size=chunk_size,
    )
    if not target_language:
        return project
    return create_profile(
        project.root,
        profile_name or _default_profile_name(target_language),
        target_language=target_language,
        target_locale=target_language,
    )


def write_manifest(project: Project, manifest: Manifest) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(project.manifest_path, manifest)


def load_manifest(project: Project) -> Manifest | None:
    primary = project.manifest_path
    legacy = _legacy_manifest_path(project)
    source_manifest = _source_manifest_path(project)
    if primary.is_file():
        path = primary
    elif project.layout_version == "profiles" and legacy.is_file():
        path = legacy
    elif project.layout_version == "legacy" and source_manifest.is_file():
        path = source_manifest
    else:
        path = primary
    if not path.is_file():
        return None
    return Manifest.model_validate_json(path.read_text("utf-8"))


def write_names(project: Project, names: NamesFile) -> None:
    from booktx.io_utils import write_json_text_atomic

    write_json_text_atomic(
        project.names_path,
        json.dumps(names.model_dump(mode="json"), indent=2, ensure_ascii=False),
    )


def load_names(project: Project) -> NamesFile:
    if not project.names_path.is_file():
        return NamesFile()
    try:
        return NamesFile.model_validate_json(project.names_path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _err("bad_names_json", f"names.json is invalid: {exc}") from exc


def protected_terms_sha256(protected_terms: list[str]) -> str:
    payload = json.dumps(
        {"protected_terms": list(protected_terms)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def project_source_sha256(project: Project) -> str:
    manifest = load_manifest(project)
    if manifest is not None and manifest.source.sha256:
        return manifest.source.sha256
    return sha256_path(find_source_file(project))


def project_source_id(project: Project) -> str:
    return f"sha256:{project_source_sha256(project)}"


def project_source_id_or_unavailable(project: Project) -> str:
    try:
        return project_source_id(project)
    except BooktxError as exc:
        if exc.code != "no_source":
            raise
        return "unavailable"


def current_source_sha256(project: Project) -> str:
    return sha256_path(find_source_file(project, persist_discovery=False))


def extracted_source_sha256(project: Project) -> str:
    manifest = load_manifest(project)
    if manifest is not None and manifest.source.sha256:
        return manifest.source.sha256
    return ""


def _require_profile_paths(project: Project, thing: str) -> None:
    if project.layout_version == "profiles" and (
        project.profile is None or project.profile_dir is None
    ):
        raise _err(
            "profile_required",
            f"{thing} requires a translation profile; pass --profile PROFILE.",
        )


def translation_store_path(project: Project) -> Path:
    if project.store_path is not None:
        return project.store_path
    _require_profile_paths(project, "translation store access")
    return project.booktx_dir / "translation-store.json"


def translation_version_ledger_path(project: Project) -> Path:
    if project.ledger_path is not None:
        return project.ledger_path
    _require_profile_paths(project, "translation version access")
    return project.booktx_dir / "translation-version-ledger.json"


def translation_source_index_path(project: Project) -> Path:
    """Profile-local generated source-only editor index path.

    Returns ``translations/<profile>/source-index.json``. The file is a
    rebuildable generated artifact, never canonical state.
    """
    _require_profile_paths(project, "translation source index access")
    assert project.profile_dir is not None
    return project.profile_dir / "source-index.json"


def translation_target_index_path(project: Project) -> Path:
    """Profile-local generated target-only editor index path.

    Returns ``translations/<profile>/target-index.json``. The file is a
    rebuildable generated artifact, never canonical state.
    """
    _require_profile_paths(project, "translation target index access")
    assert project.profile_dir is not None
    return project.profile_dir / "target-index.json"


def translation_source_target_index_path(project: Project) -> Path:
    """Profile-local generated source/target side-by-side editor index path.

    Returns ``translations/<profile>/source-target-index.json``. The file is
    a rebuildable generated artifact, never canonical state.
    """
    _require_profile_paths(project, "translation source-target index access")
    assert project.profile_dir is not None
    return project.profile_dir / "source-target-index.json"


def source_analysis_path(project: Project) -> Path:
    """Project-root canonical source-analysis JSON evidence path.

    Returns ``.booktx/source-analysis.json``. The file is rebuildable generated
    evidence (authoritative JSON report), never canonical translation state.
    """
    return project.booktx_dir / "source-analysis.json"


def source_analysis_markdown_path(project: Project) -> Path:
    """Project-root generated source-analysis Markdown view path.

    Returns ``.booktx/source-analysis.md``. Generated from the canonical JSON
    report; never authoritative.
    """
    return project.booktx_dir / "source-analysis.md"


def source_analysis_decisions_path(project: Project) -> Path:
    """Project-root durable source-analysis review/provenance sidecar."""
    return project.booktx_dir / "source-analysis-decisions.json"


def profile_source_analysis_path(project: Project) -> Path:
    """Profile-local generated source-analysis snapshot JSON path.

    Returns ``translations/<profile>/source-analysis.json``. The snapshot
    embeds the same report payload and ``analysis_sha256`` as the canonical
    project-root report inside a profile-scoped envelope. Rebuildable.
    """
    _require_profile_paths(project, "source analysis snapshot access")
    assert project.profile_dir is not None
    return project.profile_dir / "source-analysis.json"


def profile_source_analysis_markdown_path(project: Project) -> Path:
    """Profile-local generated source-analysis snapshot Markdown path.

    Returns ``translations/<profile>/source-analysis.md``. Generated from the
    profile snapshot's embedded report; never authoritative.
    """
    _require_profile_paths(project, "source analysis snapshot markdown access")
    assert project.profile_dir is not None
    return project.profile_dir / "source-analysis.md"


def context_sync_ledger_path(project: Project) -> Path:
    """Profile-local context-sync audit ledger path."""
    _require_profile_paths(project, "context sync ledger access")
    assert project.profile_dir is not None
    return project.profile_dir / "context-sync-ledger.json"


def translation_selection_ledger_path(project: Project) -> Path:
    """Profile-local selection provenance ledger path."""
    _require_profile_paths(project, "translation selection ledger access")
    assert project.profile_dir is not None
    return project.profile_dir / "translation-selection-ledger.json"


def identity_path(project: Project) -> Path:
    if project.identity_json_path is not None:
        return project.identity_json_path
    _require_profile_paths(project, "translation identity access")
    return project.booktx_dir / "identity.json"


def load_translation_store(project: Project) -> TranslationStoreV2:
    path = translation_store_path(project)
    if not path.is_file():
        return TranslationStoreV2()
    raw = json.loads(path.read_text("utf-8"))
    if raw.get("version") == 2:
        return TranslationStoreV2.model_validate(raw)
    legacy = TranslationStore.model_validate(raw)
    from booktx.progress import load_source_records
    from booktx.translation_store import legacy_store_to_v2

    source_records = {
        record.record_id: record for record in load_source_records(project)
    }
    return legacy_store_to_v2(legacy, source_records=source_records)


def load_translation_version_ledger(project: Project) -> TranslationVersionLedger:
    path = translation_version_ledger_path(project)
    if not path.is_file():
        return TranslationVersionLedger()
    return TranslationVersionLedger.model_validate_json(path.read_text("utf-8"))


def load_identity(project: Project) -> TranslationIdentity | None:
    path = identity_path(project)
    if not path.is_file():
        return None
    return TranslationIdentity.model_validate_json(path.read_text("utf-8"))


def load_context_sync_ledger(project: Project) -> ContextSyncLedger:
    path = context_sync_ledger_path(project)
    if not path.is_file():
        return ContextSyncLedger(profile=project.profile or "")
    return ContextSyncLedger.model_validate_json(path.read_text("utf-8"))


def load_translation_selection_ledger(project: Project) -> TranslationSelectionLedger:
    path = translation_selection_ledger_path(project)
    if not path.is_file():
        return TranslationSelectionLedger(
            profile=project.profile or "",
            source_sha256=project_source_sha256(project),
        )
    return TranslationSelectionLedger.model_validate_json(path.read_text("utf-8"))


def write_translation_store(project: Project, store: TranslationStoreV2) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(translation_store_path(project), store)


def write_translation_version_ledger(
    project: Project, ledger: TranslationVersionLedger
) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(translation_version_ledger_path(project), ledger)


def write_identity(project: Project, identity: TranslationIdentity) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(identity_path(project), identity)


def write_context_sync_ledger(project: Project, ledger: ContextSyncLedger) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(context_sync_ledger_path(project), ledger)


def write_translation_selection_ledger(
    project: Project, ledger: TranslationSelectionLedger
) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(translation_selection_ledger_path(project), ledger)


def translation_task_dir(project: Project) -> Path:
    if project.tasks_dir is not None:
        return project.tasks_dir
    _require_profile_paths(project, "translation task access")
    return project.booktx_dir / "tasks"


def translation_task_path(project: Project, task_id: str) -> Path:
    safe_task_id = safe_artifact_id(task_id, kind="task")
    return translation_task_dir(project) / f"{safe_task_id}.json"


def translation_task_source_block_path(project: Project, task_id: str) -> Path:
    safe_task_id = safe_artifact_id(task_id, kind="task")
    return translation_task_dir(project) / f"{safe_task_id}.source.block.txt"


def translation_ingest_dir(project: Project) -> Path:
    if project.ingest_dir is not None:
        return project.ingest_dir
    _require_profile_paths(project, "translation ingest access")
    return project.booktx_dir / "ingest"


def translation_ingest_path(project: Project, task_id: str) -> Path:
    safe_task_id = safe_artifact_id(task_id, kind="task")
    return translation_ingest_dir(project) / f"{safe_task_id}.json"


def translation_ingest_block_path(project: Project, task_id: str) -> Path:
    safe_task_id = safe_artifact_id(task_id, kind="task")
    return translation_ingest_dir(project) / f"{safe_task_id}.block.txt"


def translation_todo_dir(project: Project) -> Path:
    """Profile-local directory for durable agent-run todo files.

    Returns ``translations/<profile>/todos/``.  Raises :exc:`BooktxError`
    if no profile is selected.
    """
    _require_profile_paths(project, "translation todo access")
    return _profile_todos_dir(project.root, project.profile or "")


def _profile_todos_dir(root: Path, profile: str) -> Path:
    return profile_dir(root, profile) / "todos"


def _profile_reviews_dir(root: Path, profile: str) -> Path:
    return profile_dir(root, profile) / "reviews"


def translation_todo_json_path(project: Project, todo_id: str) -> Path:
    safe_todo_id = safe_artifact_id(todo_id, kind="todo")
    return translation_todo_dir(project) / f"{safe_todo_id}.json"


def translation_todo_markdown_path(project: Project, todo_id: str) -> Path:
    safe_todo_id = safe_artifact_id(todo_id, kind="todo")
    return translation_todo_dir(project) / f"{safe_todo_id}.md"


def _review_todo_dir(root: Path, profile: str) -> Path:
    return profile_dir(root, profile) / "review-todos"


def review_todo_dir(project: Project) -> Path:
    """Profile-local directory for durable review todo files.

    Returns ``translations/<profile>/review-todos/``.  Raises :exc:`BooktxError`
    if no profile is selected.
    """
    _require_profile_paths(project, "review todo access")
    return _review_todo_dir(project.root, project.profile or "")


def review_todo_json_path(project: Project, review_todo_id: str) -> Path:
    safe_id = safe_artifact_id(review_todo_id, kind="review_todo")
    return review_todo_dir(project) / f"{safe_id}.json"


def review_todo_markdown_path(project: Project, review_todo_id: str) -> Path:
    safe_id = safe_artifact_id(review_todo_id, kind="review_todo")
    return review_todo_dir(project) / f"{safe_id}.md"


def load_translation_task(project: Project, task_id: str) -> TranslationTask | None:
    path = translation_task_path(project, task_id)
    if not path.is_file():
        return None
    return TranslationTask.model_validate_json(path.read_text("utf-8"))


def write_translation_task(project: Project, task: TranslationTask) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(translation_task_path(project, task.task_id), task)


def translation_review_dir(project: Project) -> Path:
    """Profile-local directory for durable review task artifacts.

    Returns ``translations/<profile>/reviews/``. Raises :exc:`BooktxError` if
    no profile is selected.
    """
    _require_profile_paths(project, "translation review access")
    return profile_dir(project.root, project.profile or "") / "reviews"


def translation_review_task_path(project: Project, review_task_id: str) -> Path:
    safe = safe_artifact_id(review_task_id, kind="review_task")
    return translation_review_dir(project) / f"{safe}.json"


def translation_review_source_block_path(project: Project, review_task_id: str) -> Path:
    safe = safe_artifact_id(review_task_id, kind="review_task")
    return translation_review_dir(project) / f"{safe}.source.block.txt"


def translation_review_ingest_block_path(project: Project, review_task_id: str) -> Path:
    safe = safe_artifact_id(review_task_id, kind="review_task")
    return translation_review_dir(project) / f"{safe}.block.txt"


def load_translation_review_task(
    project: Project, review_task_id: str
) -> TranslationReviewTask | None:
    path = translation_review_task_path(project, review_task_id)
    if not path.is_file():
        return None
    return TranslationReviewTask.model_validate_json(path.read_text("utf-8"))


def write_translation_review_task(
    project: Project, task: TranslationReviewTask
) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(
        translation_review_task_path(project, task.review_task_id), task
    )


def judge_task_dir(project: Project) -> Path:
    """Profile-local directory for durable judge task artifacts."""
    _require_profile_paths(project, "judge task access")
    return _profile_judge_tasks_dir(project.root, project.profile or "")


def judge_task_path(project: Project, judge_task_id: str) -> Path:
    safe = safe_artifact_id(judge_task_id, kind="judge_task")
    return judge_task_dir(project) / f"{safe}.json"


def judge_task_source_block_path(project: Project, judge_task_id: str) -> Path:
    safe = safe_artifact_id(judge_task_id, kind="judge_task")
    return judge_task_dir(project) / f"{safe}.source.block.txt"


def judge_ingest_dir(project: Project) -> Path:
    """Profile-local directory for editable judge ingest artifacts."""
    _require_profile_paths(project, "judge ingest access")
    return _profile_judge_ingest_dir(project.root, project.profile or "")


def judge_ingest_block_path(project: Project, judge_task_id: str) -> Path:
    safe = safe_artifact_id(judge_task_id, kind="judge_task")
    return judge_ingest_dir(project) / f"{safe}.block.txt"


def judge_ingest_json_path(project: Project, judge_task_id: str) -> Path:
    safe = safe_artifact_id(judge_task_id, kind="judge_task")
    return judge_ingest_dir(project) / f"{safe}.json"


JUDGE_SOURCES_DIRNAME = "judge-sources"
JUDGE_SOURCES_MANIFEST_FILENAME = "manifest.json"
JUDGE_SOURCES_SNAPSHOT_MANIFEST_REL = "judge-sources/manifest.json"


def judge_sources_dir(project: Project) -> Path:
    """Profile-local directory holding copied judge source snapshots."""
    _require_profile_paths(project, "judge source snapshot access")
    assert project.profile_dir is not None
    return project.profile_dir / JUDGE_SOURCES_DIRNAME


def judge_sources_manifest_path(project: Project) -> Path:
    """Active-generation manifest path: ``judge-sources/manifest.json``."""
    return judge_sources_dir(project) / JUDGE_SOURCES_MANIFEST_FILENAME


def judge_sources_snapshots_dir(project: Project) -> Path:
    """Parent directory of all immutable snapshot generations."""
    return judge_sources_dir(project) / "snapshots"


def judge_source_snapshot_dir(project: Project, snapshot_id: str) -> Path:
    """Immutable directory for one snapshot generation."""
    validate_snapshot_id(snapshot_id)
    return judge_sources_snapshots_dir(project) / snapshot_id


def judge_source_profile_dir(
    project: Project, snapshot_id: str, source_profile: str
) -> Path:
    """Per-source profile directory inside one snapshot generation."""
    validate_snapshot_id(snapshot_id)
    validate_profile_name(source_profile)
    return judge_source_snapshot_dir(project, snapshot_id) / "profiles" / source_profile


def judge_source_profile_config_path(
    project: Project, snapshot_id: str, source_profile: str
) -> Path:
    return (
        judge_source_profile_dir(project, snapshot_id, source_profile)
        / "profile-config.json"
    )


def judge_source_translation_store_path(
    project: Project, snapshot_id: str, source_profile: str
) -> Path:
    return (
        judge_source_profile_dir(project, snapshot_id, source_profile)
        / "translation-store.json"
    )


def judge_source_translation_version_ledger_path(
    project: Project, snapshot_id: str, source_profile: str
) -> Path:
    return (
        judge_source_profile_dir(project, snapshot_id, source_profile)
        / "translation-version-ledger.json"
    )


def judge_source_identity_path(
    project: Project, snapshot_id: str, source_profile: str
) -> Path:
    return (
        judge_source_profile_dir(project, snapshot_id, source_profile) / "identity.json"
    )


def load_judge_task(project: Project, judge_task_id: str) -> JudgeTask | None:
    path = judge_task_path(project, judge_task_id)
    if not path.is_file():
        return None
    return JudgeTask.model_validate_json(path.read_text("utf-8"))


def write_judge_task(project: Project, task: JudgeTask) -> None:
    from booktx.io_utils import write_json_model_atomic

    write_json_model_atomic(judge_task_path(project, task.judge_task_id), task)


def _persist_source_config(project: Project) -> None:
    if project.layout_version == "legacy":
        legacy_cfg = ProjectConfig(
            source_language=project.config.source_language,
            target_language=project.config.target_language,
            source_file=project.config.source_file,
            format=project.config.format,
            chunk_size=project.config.chunk_size,
        )
        _write_legacy_config(project.config_path, legacy_cfg)
    else:
        _write_source_config(project.source_config_path, project.source_config)


def find_source_file(project: Project, *, persist_discovery: bool = True) -> Path:
    """Resolve the project source file.

    By default this **persists** a newly discovered source file/format back into
    the source config (a write side effect). Pass ``persist_discovery=False``
    for read-only lookups such as status overviews and hashing, so a getter no
    longer mutates project state.
    """
    configured = project.config.source_file.strip()
    if configured:
        candidate = project.source_dir / configured
        if candidate.is_file():
            return candidate

    candidates = [
        path
        for path in sorted(project.source_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    ]
    if not candidates:
        raise _err(
            "no_source",
            f"No source document found in {project.source_dir}. Drop a .md or .epub file into source/.",
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise _err(
            "ambiguous_source",
            f"Found multiple source documents in {project.source_dir}: {names}. Keep exactly one.",
        )
    chosen = candidates[0]
    chosen_format = detect_format(chosen.name)
    if persist_discovery and (
        project.config.source_file != chosen.name
        or project.config.format != chosen_format
    ):
        project.config.source_file = chosen.name
        project.config.format = chosen_format
        project.source_config.source_file = chosen.name
        project.source_config.format = chosen_format
        _persist_source_config(project)
    return chosen
    return chosen


# Legacy single-layout -> profile migration lives in its own module to
# quarantine the compatibility surface. Re-exported here for import
# compatibility (``from booktx.config import migrate_current_project``).
from booktx.profile_migration import (  # noqa: E402,F401
    MigrationMove,
    ProfileMigrationPlan,
    build_profile_migration_plan,
    migrate_current_project,
)

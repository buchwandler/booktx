"""Translation preference dictionary models and shard storage helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from booktx.config import (
    Project,
    _err,
    canonical_language_key,
    global_lexicon_path,
    lexicon_language_keys,
    profile_lexicon_path,
    project_lexicon_path,
)
from booktx.io_utils import utc_timestamp, write_json_text_atomic
from booktx.path_ids import safe_artifact_id

__all__ = [
    "LEXICON_REGEX_MAX_LENGTH",
    "LexiconExample",
    "LexiconProvenance",
    "LexiconEntry",
    "TranslationLexicon",
    "EffectiveTranslationLexicon",
    "ResolvedLexiconLayer",
    "canonical_lexicon_json",
    "create_lexicon_backup",
    "effective_approved_entries",
    "infer_mutation_language_key",
    "load_lexicon_shard",
    "load_optional_lexicon_shard",
    "merge_effective_lexicon",
    "resolve_effective_lexicon",
    "resolved_lexicon_layers",
    "write_lexicon_shard",
]

LEXICON_REGEX_MAX_LENGTH = 512


def _parse_utc_timestamp(value: str, *, field_name: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - pydantic exposes the message
        raise ValueError(f"{field_name} must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must be an RFC 3339 UTC timestamp")
    return (
        parsed.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _strip_nonempty(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _dedupe_nonempty(values: list[str], *, field_name: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must not contain empty values")
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _validate_regex(pattern: str, *, field_name: str) -> str:
    cleaned = pattern.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > LEXICON_REGEX_MAX_LENGTH:
        raise ValueError(
            f"{field_name} exceeds the maximum length of {LEXICON_REGEX_MAX_LENGTH}"
        )
    try:
        re.compile(cleaned)
    except re.error as exc:
        raise ValueError(f"{field_name} is invalid: {exc}") from exc
    return cleaned


class LexiconExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    good_target: str = ""
    bad_target: str = ""
    note: str = ""

    @field_validator("source")
    @classmethod
    def _source_nonempty(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="source")

    @field_validator("good_target", "bad_target", "note")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()


class LexiconProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_source_id: str = ""
    source_title: str = ""
    profile: str = ""
    record_id: str = ""
    source_sha256: str = ""
    target_before: str = ""
    target_after: str = ""
    note: str = ""

    @field_validator(
        "project_source_id",
        "source_title",
        "profile",
        "record_id",
        "source_sha256",
        "target_before",
        "target_after",
        "note",
    )
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()


class LexiconEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["approved", "draft", "rejected", "disabled"] = "approved"
    kind: Literal[
        "phrase_preference",
        "collocation_preference",
        "word_sense",
        "style_preference",
        "forbidden_literalism",
        "world_term",
    ] = "phrase_preference"

    source: str
    source_variants: list[str] = Field(default_factory=list)
    source_regex: str | None = None
    source_language: str = "en"
    case_sensitive: bool = False

    target_preferred: list[str] = Field(default_factory=list)
    target_allowed: list[str] = Field(default_factory=list)
    target_forbidden: list[str] = Field(default_factory=list)
    target_regex_forbidden: list[str] = Field(default_factory=list)
    preferred_policy: Literal["off", "advisory", "required"] = "off"
    target_language: str = "de"
    target_locale: str = ""

    sense: str = ""
    rationale: str = ""
    examples: list[LexiconExample] = Field(default_factory=list)

    severity: Literal["info", "warn", "error"] = "warn"
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    created_by_kind: Literal["user", "model", "import", "unknown"] = "unknown"
    provenance: list[LexiconProvenance] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return safe_artifact_id(value.strip(), kind="lexicon_entry")

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="source")

    @field_validator("source_language", "target_language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        return canonical_language_key(value)

    @field_validator("target_locale")
    @classmethod
    def _validate_locale(cls, value: str) -> str:
        cleaned = value.strip()
        return canonical_language_key(cleaned) if cleaned else ""

    @field_validator("source_variants")
    @classmethod
    def _validate_source_variants(cls, value: list[str]) -> list[str]:
        return _dedupe_nonempty(value, field_name="source_variants")

    @field_validator(
        "target_preferred",
        "target_allowed",
        "target_forbidden",
        "target_regex_forbidden",
    )
    @classmethod
    def _validate_target_lists(cls, value: list[str], info) -> list[str]:
        field_name = str(info.field_name)
        if field_name == "target_regex_forbidden":
            deduped = _dedupe_nonempty(value, field_name=field_name)
            return [_validate_regex(item, field_name=field_name) for item in deduped]
        return _dedupe_nonempty(value, field_name=field_name)

    @field_validator("source_regex")
    @classmethod
    def _validate_source_regex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_regex(value, field_name="source_regex")

    @field_validator("sense", "rationale", "created_by")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timestamps(cls, value: str, info) -> str:
        return _parse_utc_timestamp(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def _validate_matching_cues(self) -> LexiconEntry:
        if self.source_regex is None and not self.source and not self.source_variants:
            raise ValueError("lexicon entry requires at least one source cue")
        return self


class TranslationLexicon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    language_key: str
    source_language: str | None = None
    target_language: str
    target_locale: str = ""
    entries: list[LexiconEntry] = Field(default_factory=list)

    @field_validator("language_key")
    @classmethod
    def _validate_language_key(cls, value: str) -> str:
        return canonical_language_key(value)

    @field_validator("source_language")
    @classmethod
    def _validate_optional_source_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return canonical_language_key(value)

    @field_validator("target_language")
    @classmethod
    def _validate_target_language(cls, value: str) -> str:
        return canonical_language_key(value)

    @field_validator("target_locale")
    @classmethod
    def _validate_target_locale(cls, value: str) -> str:
        cleaned = value.strip()
        return canonical_language_key(cleaned) if cleaned else ""

    @model_validator(mode="after")
    def _validate_entries(self) -> TranslationLexicon:
        expected_base = self.target_language
        expected_language_key = (
            expected_base if not self.target_locale else self.target_locale
        )
        if self.language_key != expected_language_key:
            raise ValueError(
                "language_key must match the shard target language/locale contract"
            )
        seen_ids: set[str] = set()
        for entry in self.entries:
            if entry.id in seen_ids:
                raise ValueError(f"duplicate lexicon entry id: {entry.id}")
            seen_ids.add(entry.id)
            if (
                self.source_language is not None
                and entry.source_language != self.source_language
            ):
                raise ValueError(
                    f"entry {entry.id} source_language must match shard source_language"
                )
            if entry.target_language != self.target_language:
                raise ValueError(
                    f"entry {entry.id} target_language must match shard target_language"
                )
            if entry.target_locale != self.target_locale:
                raise ValueError(
                    f"entry {entry.id} target_locale must match shard target_locale"
                )
        return self


class EffectiveTranslationLexicon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_keys: list[str]
    source_language: str | None = None
    target_language: str
    target_locale: str = ""
    entries: list[LexiconEntry] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResolvedLexiconLayer:
    scope: Literal["global", "project", "profile"]
    language_key: str
    path: Path
    shard: TranslationLexicon | None

    @property
    def exists(self) -> bool:
        return self.shard is not None


def _effective_target_parts(language_keys: list[str]) -> tuple[str, str]:
    if not language_keys:
        raise _err("lexicon_language_required", "at least one language key is required")
    base = canonical_language_key(language_keys[0])
    locale = ""
    if len(language_keys) > 1:
        locale = canonical_language_key(language_keys[-1])
    return base, locale


def canonical_lexicon_json(lexicon: TranslationLexicon) -> str:
    """Serialize one shard as canonical, stable JSON."""
    payload = lexicon.model_dump(mode="json")
    payload["entries"] = sorted(payload["entries"], key=lambda item: item["id"])
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_lexicon_shard(
    path: Path, *, expected_language_key: str | None = None
) -> TranslationLexicon:
    """Load one lexicon shard and validate its filename contract."""
    shard = TranslationLexicon.model_validate_json(path.read_text("utf-8"))
    if (
        expected_language_key is not None
        and shard.language_key != canonical_language_key(expected_language_key)
    ):
        raise _err(
            "lexicon_language_mismatch",
            f"lexicon shard language_key {shard.language_key!r} does not match "
            f"expected {canonical_language_key(expected_language_key)!r}",
        )
    stem = canonical_language_key(path.stem)
    if shard.language_key != stem:
        raise _err(
            "lexicon_filename_mismatch",
            f"lexicon shard language_key {shard.language_key!r} does not match "
            f"filename {path.name!r}",
        )
    return shard


def load_optional_lexicon_shard(
    path: Path, *, expected_language_key: str | None = None
) -> TranslationLexicon | None:
    if not path.is_file():
        return None
    return load_lexicon_shard(path, expected_language_key=expected_language_key)


def write_lexicon_shard(path: Path, lexicon: TranslationLexicon) -> None:
    """Write one lexicon shard with canonical JSON ordering."""
    write_json_text_atomic(path, canonical_lexicon_json(lexicon))


def create_lexicon_backup(path: Path) -> Path:
    """Create a collision-resistant backup next to ``path`` and return it."""
    if not path.is_file():
        raise _err("lexicon_backup_missing", f"cannot back up missing shard: {path}")
    timestamp = utc_timestamp().replace(":", "").replace("-", "")
    digest = sha256(path.read_bytes()).hexdigest()[:8]
    backup = path.with_name(f"{path.stem}.{timestamp}.{digest}.bak.json")
    backup.write_text(path.read_text("utf-8"), encoding="utf-8")
    return backup


def _global_exact_shard_path(language_key: str) -> Path:
    key = canonical_language_key(language_key)
    override = os.environ.get("BOOKTX_LEXICON_PATH", "").strip()
    if override:
        if "-" in key:
            raise _err(
                "lexicon_exact_path_invalid",
                "BOOKTX_LEXICON_PATH is valid only for exact base-language "
                "global shard operations",
            )
        return Path(override).expanduser().resolve()
    return global_lexicon_path(key)


def infer_mutation_language_key(project: Project, language: str | None = None) -> str:
    """Choose the destination shard for add/import/profile mutations."""
    if language is not None:
        return canonical_language_key(language)
    cfg = project.profile_config
    if cfg is None:
        raise _err(
            "lexicon_language_required",
            "--language is required when no profile target language is available",
        )
    locale_raw = (cfg.target_locale or "").strip()
    if locale_raw:
        locale = canonical_language_key(locale_raw)
        if locale != canonical_language_key(cfg.target_language):
            return locale
    return canonical_language_key(cfg.target_language)


def resolved_lexicon_layers(
    project: Project | None,
    *,
    language_keys: list[str],
    scope: Literal["global", "project", "profile", "effective"],
    allow_global_exact_override: bool = False,
) -> list[ResolvedLexiconLayer]:
    """Resolve shard paths for the requested scope and language sequence."""
    if scope in {"project", "profile", "effective"} and project is None:
        raise _err("lexicon_project_required", "this lexicon scope requires a project")
    layers: list[ResolvedLexiconLayer] = []
    include_global = scope in {"global", "effective"}
    include_project = scope in {"project", "effective"}
    include_profile = scope in {"profile", "effective"}
    ordered_scopes: list[str] = []
    if include_global:
        ordered_scopes.append("global")
    if include_project:
        ordered_scopes.append("project")
    if include_profile:
        ordered_scopes.append("profile")
    for layer_scope in ordered_scopes:
        for key in language_keys:
            if layer_scope == "global":
                if (
                    allow_global_exact_override
                    and len(language_keys) == 1
                    and key == language_keys[0]
                ):
                    path = _global_exact_shard_path(key)
                else:
                    path = global_lexicon_path(key)
                layers.append(
                    ResolvedLexiconLayer(
                        scope="global",
                        language_key=key,
                        path=path,
                        shard=load_optional_lexicon_shard(
                            path, expected_language_key=key
                        ),
                    )
                )
                continue
            if project is None:
                continue
            if layer_scope == "project":
                path = project_lexicon_path(project, key)
                layers.append(
                    ResolvedLexiconLayer(
                        scope="project",
                        language_key=key,
                        path=path,
                        shard=load_optional_lexicon_shard(
                            path, expected_language_key=key
                        ),
                    )
                )
                continue
            path = profile_lexicon_path(project, key)
            layers.append(
                ResolvedLexiconLayer(
                    scope="profile",
                    language_key=key,
                    path=path,
                    shard=load_optional_lexicon_shard(path, expected_language_key=key),
                )
            )
    return layers


def merge_effective_lexicon(
    layers: list[ResolvedLexiconLayer], *, language_keys: list[str]
) -> EffectiveTranslationLexicon:
    """Merge resolved layers into one effective lexicon with whole-entry overrides."""
    target_language, target_locale = _effective_target_parts(language_keys)
    resolved: dict[str, LexiconEntry] = {}
    source_language: str | None = None
    for layer in layers:
        if layer.shard is None:
            continue
        if source_language is None and layer.shard.source_language is not None:
            source_language = layer.shard.source_language
        for entry in layer.shard.entries:
            resolved[entry.id] = entry.model_copy(deep=True)
    return EffectiveTranslationLexicon(
        language_keys=list(language_keys),
        source_language=source_language,
        target_language=target_language,
        target_locale=target_locale,
        entries=sorted(resolved.values(), key=lambda item: item.id),
    )


def resolve_effective_lexicon(
    project: Project,
    *,
    language: str | None = None,
) -> tuple[EffectiveTranslationLexicon, list[ResolvedLexiconLayer]]:
    """Load and merge the effective lexicon for a project/profile runtime."""
    language_keys = lexicon_language_keys(project, language)
    layers = resolved_lexicon_layers(
        project, language_keys=language_keys, scope="effective"
    )
    return merge_effective_lexicon(layers, language_keys=language_keys), layers


def effective_approved_entries(
    effective: EffectiveTranslationLexicon,
    *,
    source_language: str | None = None,
) -> list[LexiconEntry]:
    """Return effective entries that participate in matching."""
    entries = [entry for entry in effective.entries if entry.status == "approved"]
    if source_language is None:
        return entries
    source = canonical_language_key(source_language)
    return [entry for entry in entries if entry.source_language == source]

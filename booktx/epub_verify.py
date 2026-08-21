"""Read-only access to and verification of generated EPUB ZIP archives."""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from booktx.errors import BooktxError

__all__ = [
    "EpubArchive",
    "inspect_epub_archive",
    "normalize_epub_href",
]


def normalize_epub_href(value: str) -> str:
    """Normalize an EPUB href for comparison with a ZIP member name."""
    value = unquote(value.split("#", 1)[0].split("?", 1)[0])
    return posixpath.normpath(value.lstrip("/"))


@dataclass(frozen=True, slots=True)
class EpubArchive:
    """A read-only view of a generated EPUB archive."""

    path: Path
    entries: tuple[str, ...]

    @classmethod
    def open(cls, path: Path) -> EpubArchive:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = tuple(archive.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            raise BooktxError(
                "invalid_epub_output",
                f"built EPUB is not a readable ZIP archive: {path}",
            ) from exc
        return cls(path=path, entries=entries)

    def read_bytes(self, name: str) -> bytes:
        try:
            with zipfile.ZipFile(self.path) as archive:
                return archive.read(name)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise BooktxError(
                "invalid_epub_output",
                f"could not read EPUB entry {name!r} from {self.path}",
            ) from exc

    def read_text(self, name: str) -> str:
        return self.read_bytes(name).decode("utf-8", errors="replace")

    def testzip(self) -> str | None:
        """Return the first member with a failed CRC, if any."""
        try:
            with zipfile.ZipFile(self.path) as archive:
                return archive.testzip()
        except (OSError, zipfile.BadZipFile) as exc:
            raise BooktxError(
                "invalid_epub_output",
                f"could not verify EPUB archive: {self.path}",
            ) from exc

    def xhtml_entries(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.entries if name.lower().endswith((".xhtml", ".html"))
        )

    def resolve_entry(self, href: str) -> str | None:
        """Resolve a manifest href to one archive member."""
        normalized = normalize_epub_href(href)
        exact = {normalize_epub_href(name): name for name in self.entries}
        if normalized in exact:
            return exact[normalized]
        suffix_matches = [
            name
            for name in self.entries
            if normalize_epub_href(name).endswith("/" + normalized)
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        return None


def _opf_path(archive: EpubArchive) -> str | None:
    """Resolve the package document from ``META-INF/container.xml``."""
    if "META-INF/container.xml" not in archive.entries:
        return None
    try:
        root = ET.fromstring(archive.read_bytes("META-INF/container.xml"))
    except (ET.ParseError, UnicodeError):
        return None
    rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    if rootfile is None:
        return None
    full_path = rootfile.attrib.get("full-path", "")
    return archive.resolve_entry(full_path)


def inspect_epub_archive(path: Path) -> dict[str, object]:
    """Return structural and placeholder checks for one EPUB artifact."""
    archive = EpubArchive.open(path)
    crc_entry = archive.testzip()
    mimetype: str | None = None
    mimetype_stored = False
    if "mimetype" in archive.entries:
        mimetype = archive.read_bytes("mimetype").decode("ascii", errors="replace")
        with zipfile.ZipFile(path) as zip_file:
            mimetype_stored = (
                zip_file.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
            )

    try:
        from text2epub.validation import scan_epub_for_unresolved_tokens

        unresolved = scan_epub_for_unresolved_tokens(path)
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise BooktxError(
            "epub_scan_failed", f"could not scan EPUB text entries: {path}"
        ) from exc

    return {
        "valid": True,
        "crc_ok": crc_entry is None,
        "crc_error_entry": crc_entry,
        "mimetype": mimetype,
        "mimetype_valid": mimetype == "application/epub+zip",
        "mimetype_stored": mimetype_stored,
        "opf_path": _opf_path(archive),
        "opf_resolvable": _opf_path(archive) is not None,
        "entry_count": len(archive.entries),
        "xhtml_entry_count": len(archive.xhtml_entries()),
        "unresolved_tokens": [
            {"entry": entry, "token": token} for entry, token in unresolved
        ],
        "unresolved_count": len(unresolved),
    }

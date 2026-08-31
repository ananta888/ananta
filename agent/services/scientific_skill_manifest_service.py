"""Read-only scientific skill package inventory; package content is never executed."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import tarfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit

import yaml

_PIN = re.compile(r"^(?:[0-9a-f]{7,64}|v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".js", ".mjs", ".ts", ".r", ".R"})
AUTHORIZED_UPSTREAM_REPOSITORY = "https://github.com/K-Dense-AI/scientific-agent-skills"


class ScientificSkillManifestError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class LoadedScientificSkillPackage:
    files: Mapping[str, bytes]


class ScientificSkillPackageLoaderPort(Protocol):
    def load(self, package_path: Path) -> LoadedScientificSkillPackage: ...


@dataclass(frozen=True)
class ScientificSkillFileMetadata:
    relative_path: str
    sha256: str
    size_bytes: int
    kind: str
    language: str | None


@dataclass(frozen=True)
class ScientificSkillManifest:
    name: str
    description: str
    upstream_path: str
    upstream_pin: str
    sha256: str
    license: str
    declared_files: tuple[ScientificSkillFileMetadata, ...]
    declared_capabilities: tuple[str, ...]
    declared_dependencies: tuple[str, ...]
    declared_data_classification: str
    source_references: tuple[str, ...]
    parser_warnings: tuple[str, ...]


@dataclass(frozen=True)
class ScientificSkillPackageManifest:
    upstream_repository: str
    upstream_pin: str
    package_name: str
    license: str
    package_sha256: str
    skills: tuple[ScientificSkillManifest, ...]


class LocalScientificSkillPackageLoader:
    """Loads a directory, ZIP, or TAR into a bounded in-memory read model."""

    MAX_SOURCE_BYTES = 32 * 1024 * 1024
    MAX_FILES = 5_000
    MAX_FILE_BYTES = 1 * 1024 * 1024
    MAX_TOTAL_BYTES = 32 * 1024 * 1024
    MAX_ARCHIVE_RATIO = 100

    def load(self, package_path: Path) -> LoadedScientificSkillPackage:
        path = package_path.resolve()
        if path.is_dir():
            return LoadedScientificSkillPackage(self._load_directory(path))
        if not path.is_file() or path.stat().st_size > self.MAX_SOURCE_BYTES:
            raise ScientificSkillManifestError("scientific_skill_package_invalid")
        if zipfile.is_zipfile(path):
            return LoadedScientificSkillPackage(self._load_zip(path))
        if tarfile.is_tarfile(path):
            return LoadedScientificSkillPackage(self._load_tar(path))
        raise ScientificSkillManifestError("scientific_skill_archive_format_denied")

    def _load_directory(self, root: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                raise ScientificSkillManifestError("scientific_skill_symlink_denied")
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            _validated_relative_path(relative)
            with candidate.open("rb") as handle:
                content = handle.read(self.MAX_FILE_BYTES + 1)
            total = self._validate_budget(len(files) + 1, total, len(content))
            files[relative] = content
        return files

    def _load_zip(self, path: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            compressed = max(1, sum(entry.compress_size for entry in entries))
            expanded = sum(entry.file_size for entry in entries)
            if expanded / compressed > self.MAX_ARCHIVE_RATIO:
                raise ScientificSkillManifestError("scientific_skill_archive_budget_exceeded")
            for entry in entries:
                if entry.is_dir():
                    continue
                relative = _validated_relative_path(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ScientificSkillManifestError("scientific_skill_symlink_denied")
                total = self._validate_budget(len(files) + 1, total, entry.file_size)
                if relative in files:
                    raise ScientificSkillManifestError("scientific_skill_duplicate_path")
                content = archive.read(entry)
                if len(content) != entry.file_size:
                    raise ScientificSkillManifestError("scientific_skill_archive_entry_invalid")
                files[relative] = content
        return files

    def _load_tar(self, path: Path) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        with tarfile.open(path, mode="r:*") as archive:
            entries = archive.getmembers()
            expanded = sum(entry.size for entry in entries if entry.isfile())
            if expanded / max(1, path.stat().st_size) > self.MAX_ARCHIVE_RATIO:
                raise ScientificSkillManifestError("scientific_skill_archive_budget_exceeded")
            for entry in entries:
                if entry.issym() or entry.islnk():
                    raise ScientificSkillManifestError("scientific_skill_symlink_denied")
                if entry.isdir():
                    continue
                if not entry.isfile():
                    raise ScientificSkillManifestError("scientific_skill_archive_entry_denied")
                relative = _validated_relative_path(entry.name)
                total = self._validate_budget(len(files) + 1, total, entry.size)
                if relative in files:
                    raise ScientificSkillManifestError("scientific_skill_duplicate_path")
                extracted = archive.extractfile(entry)
                if extracted is None:
                    raise ScientificSkillManifestError("scientific_skill_archive_entry_invalid")
                content = extracted.read(self.MAX_FILE_BYTES + 1)
                if len(content) != entry.size:
                    raise ScientificSkillManifestError("scientific_skill_archive_entry_invalid")
                files[relative] = content
        return files

    def _validate_budget(self, count: int, total: int, size: int) -> int:
        updated = total + size
        if count > self.MAX_FILES or size > self.MAX_FILE_BYTES or updated > self.MAX_TOTAL_BYTES:
            raise ScientificSkillManifestError("scientific_skill_archive_budget_exceeded")
        return updated


class ScientificSkillManifestImporter:
    """Parses declarative skill metadata from a loader-owned package snapshot."""

    def __init__(self, loader: ScientificSkillPackageLoaderPort | None = None) -> None:
        self._loader = loader or LocalScientificSkillPackageLoader()

    def inspect(
        self,
        *,
        package_path: Path,
        upstream_repository: str,
        upstream_pin: str,
    ) -> ScientificSkillPackageManifest:
        if upstream_repository != AUTHORIZED_UPSTREAM_REPOSITORY:
            raise ScientificSkillManifestError("scientific_skill_repository_invalid")
        if not isinstance(upstream_pin, str) or not _PIN.fullmatch(upstream_pin):
            raise ScientificSkillManifestError("scientific_skill_pin_invalid")
        package = self._loader.load(package_path)
        files = _normalized_package_root(dict(package.files))
        plugin_path = self._plugin_path(files)
        plugin = _json_mapping(files[plugin_path], reason="scientific_skill_plugin_invalid")
        package_name = _required_name(plugin.get("name"), "scientific_skill_plugin_name_invalid")
        license_name = _license_name(plugin, files)
        skill_paths = tuple(sorted(path for path in files if PurePosixPath(path).name == "SKILL.md"))
        if not skill_paths:
            raise ScientificSkillManifestError("scientific_skill_definition_missing")
        skills = tuple(
            self._parse_skill(
                skill_path,
                files=files,
                upstream_pin=upstream_pin,
                license_name=license_name,
            )
            for skill_path in skill_paths
        )
        package_digest = _file_set_digest(files)
        return ScientificSkillPackageManifest(
            upstream_repository=upstream_repository,
            upstream_pin=upstream_pin,
            package_name=package_name,
            license=license_name,
            package_sha256=package_digest,
            skills=skills,
        )

    @staticmethod
    def _plugin_path(files: Mapping[str, bytes]) -> str:
        candidates = tuple(
            path
            for path in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")
            if path in files
        )
        if len(candidates) != 1:
            raise ScientificSkillManifestError("scientific_skill_plugin_missing_or_ambiguous")
        return candidates[0]

    @staticmethod
    def _parse_skill(
        skill_path: str,
        *,
        files: Mapping[str, bytes],
        upstream_pin: str,
        license_name: str,
    ) -> ScientificSkillManifest:
        text = _text_file(files[skill_path])
        frontmatter, body = _frontmatter(text)
        name = _required_name(frontmatter.get("name"), "scientific_skill_name_invalid")
        description = frontmatter.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 2_000:
            raise ScientificSkillManifestError("scientific_skill_description_invalid")
        capabilities, dependencies, data_classification = _ananta_declarations(frontmatter.get("metadata"))
        local_paths, external_references = _declared_links(body, base_path=skill_path)
        declared = {skill_path, *local_paths}
        skill_directory = PurePosixPath(skill_path).parent
        scripts_prefix = f"{skill_directory.as_posix()}/scripts/"
        declared.update(path for path in files if path.startswith(scripts_prefix))
        missing = sorted(path for path in declared if path not in files)
        if missing:
            raise ScientificSkillManifestError("scientific_skill_reference_unresolved")
        metadata: list[ScientificSkillFileMetadata] = []
        warnings: list[str] = []
        if set(frontmatter) - {"name", "description", "metadata", "license", "compatibility"}:
            warnings.append("frontmatter_unknown_fields")
        for path in sorted(declared):
            content = files[path]
            _text_file(content)
            suffix = PurePosixPath(path).suffix
            is_script = suffix in _SCRIPT_SUFFIXES or "/scripts/" in f"/{path}"
            metadata.append(
                ScientificSkillFileMetadata(
                    relative_path=path,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    kind="script" if is_script else ("skill" if path == skill_path else "reference"),
                    language=_script_language(suffix) if is_script else None,
                )
            )
        skill_digest = _file_metadata_digest(metadata)
        return ScientificSkillManifest(
            name=name,
            description=description.strip(),
            upstream_path=skill_path,
            upstream_pin=upstream_pin,
            sha256=skill_digest,
            license=license_name,
            declared_files=tuple(metadata),
            declared_capabilities=capabilities,
            declared_dependencies=dependencies,
            declared_data_classification=data_classification,
            source_references=tuple(sorted(external_references)),
            parser_warnings=tuple(warnings),
        )


def _validated_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and path.parts[0].endswith(":"))
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ScientificSkillManifestError("scientific_skill_path_traversal_denied")
    return path.as_posix()


def _normalized_package_root(files: dict[str, bytes]) -> dict[str, bytes]:
    plugin_paths = {"plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json"}
    if plugin_paths.intersection(files):
        return files
    roots = {PurePosixPath(path).parts[0] for path in files if PurePosixPath(path).parts}
    if len(roots) != 1:
        raise ScientificSkillManifestError("scientific_skill_plugin_missing_or_ambiguous")
    prefix = f"{next(iter(roots))}/"
    normalized = {path.removeprefix(prefix): content for path, content in files.items()}
    if not plugin_paths.intersection(normalized):
        raise ScientificSkillManifestError("scientific_skill_plugin_missing_or_ambiguous")
    return normalized


def _license_name(plugin: Mapping[str, object], files: Mapping[str, bytes]) -> str:
    declared = plugin.get("license")
    if isinstance(declared, str) and declared.strip() and len(declared) <= 128:
        return declared.strip()
    if declared is not None:
        raise ScientificSkillManifestError("scientific_skill_license_invalid")
    license_paths = tuple(path for path in ("LICENSE", "LICENSE.md", "LICENSE.txt") if path in files)
    if len(license_paths) == 1 and "MIT License" in _text_file(files[license_paths[0]])[:4_096]:
        return "MIT"
    raise ScientificSkillManifestError("scientific_skill_license_invalid")


def _json_mapping(content: bytes, *, reason: str) -> Mapping[str, object]:
    try:
        value = json.loads(_text_file(content))
    except json.JSONDecodeError as exc:
        raise ScientificSkillManifestError(reason) from exc
    if not isinstance(value, Mapping):
        raise ScientificSkillManifestError(reason)
    return value


def _frontmatter(text: str) -> tuple[Mapping[str, object], str]:
    if not text.startswith("---\n"):
        raise ScientificSkillManifestError("scientific_skill_frontmatter_invalid")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ScientificSkillManifestError("scientific_skill_frontmatter_invalid")
    try:
        value = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise ScientificSkillManifestError("scientific_skill_frontmatter_invalid") from exc
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ScientificSkillManifestError("scientific_skill_frontmatter_invalid")
    return value, text[end + 5 :]


def _declared_links(body: str, *, base_path: str) -> tuple[frozenset[str], frozenset[str]]:
    local: set[str] = set()
    external: set[str] = set()
    base = PurePosixPath(base_path).parent
    for raw_target in _MARKDOWN_LINK.findall(body):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https"}:
            external.add(target)
            continue
        if parsed.scheme or parsed.netloc:
            raise ScientificSkillManifestError("scientific_skill_reference_scheme_denied")
        decoded = unquote(parsed.path)
        if not decoded:
            continue
        if PurePosixPath(decoded).is_absolute():
            raise ScientificSkillManifestError("scientific_skill_path_traversal_denied")
        local.add(_validated_relative_path((base / decoded).as_posix()))
    return frozenset(local), frozenset(external)


def _required_name(value: object, reason: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ScientificSkillManifestError(reason)
    return value


def _ananta_declarations(metadata: object) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    if metadata is None:
        return (), (), "internal"
    if not isinstance(metadata, Mapping):
        raise ScientificSkillManifestError("scientific_skill_metadata_invalid")
    ananta = metadata.get("ananta")
    if ananta is None:
        return (), (), "internal"
    if not isinstance(ananta, Mapping) or set(ananta) - {
        "capabilities",
        "dependencies",
        "data_classification",
    }:
        raise ScientificSkillManifestError("scientific_skill_metadata_invalid")
    capabilities = _identifier_list(ananta.get("capabilities", ()))
    dependencies = _dependency_list(ananta.get("dependencies", ()))
    classification = ananta.get("data_classification", "internal")
    if classification not in {"public", "internal", "confidential", "restricted"}:
        raise ScientificSkillManifestError("scientific_skill_metadata_invalid")
    return capabilities, dependencies, classification


def _identifier_list(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list | tuple)
        or any(not isinstance(item, str) or not _NAME.fullmatch(item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise ScientificSkillManifestError("scientific_skill_metadata_invalid")
    return tuple(sorted(value))


def _dependency_list(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list | tuple)
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z0-9@][A-Za-z0-9@_./:-]{0,127}", item)
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ScientificSkillManifestError("scientific_skill_metadata_invalid")
    return tuple(sorted(value))


def _text_file(content: bytes) -> str:
    if not isinstance(content, bytes) or len(content) > LocalScientificSkillPackageLoader.MAX_FILE_BYTES:
        raise ScientificSkillManifestError("scientific_skill_file_size_invalid")
    if b"\x00" in content:
        raise ScientificSkillManifestError("scientific_skill_binary_file_denied")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScientificSkillManifestError("scientific_skill_binary_file_denied") from exc


def _file_set_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(files[path]).digest())
    return digest.hexdigest()


def _file_metadata_digest(files: list[ScientificSkillFileMetadata]) -> str:
    projection = [
        {"path": item.relative_path, "sha256": item.sha256, "size_bytes": item.size_bytes}
        for item in files
    ]
    return hashlib.sha256(
        json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _script_language(suffix: str) -> str:
    return {
        ".py": "python",
        ".sh": "shell",
        ".bash": "shell",
        ".js": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".r": "r",
        ".R": "r",
    }.get(suffix, "unknown")


__all__ = [
    "AUTHORIZED_UPSTREAM_REPOSITORY",
    "LoadedScientificSkillPackage",
    "LocalScientificSkillPackageLoader",
    "ScientificSkillFileMetadata",
    "ScientificSkillManifest",
    "ScientificSkillManifestError",
    "ScientificSkillManifestImporter",
    "ScientificSkillPackageLoaderPort",
    "ScientificSkillPackageManifest",
]

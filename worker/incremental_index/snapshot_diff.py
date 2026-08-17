"""CodeCompass Snapshot Diff Service — computes canonical ChangeSets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class FileChange:
    """A single file-level change in the ChangeSet."""

    operation: Literal["add", "modify", "delete", "rename", "metadata_only"]
    path: str
    new_path: str | None = None
    old_content_sha256: str | None = None
    new_content_sha256: str | None = None
    old_byte_size: int | None = None
    new_byte_size: int | None = None
    outcome_changed: bool = False
    support_level_changed: bool = False
    extractor_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "path": self.path,
            "new_path": self.new_path,
            "old_content_sha256": self.old_content_sha256,
            "new_content_sha256": self.new_content_sha256,
            "old_byte_size": self.old_byte_size,
            "new_byte_size": self.new_byte_size,
            "outcome_changed": self.outcome_changed,
            "support_level_changed": self.support_level_changed,
            "extractor_changed": self.extractor_changed,
        }


@dataclass
class PolicyTransition:
    """Detected policy/configuration transition between snapshots."""

    transition_type: Literal[
        "profile_change", "registry_change", "pipeline_change", "budget_change", "required_paths_change"
    ]
    old_value: Any = None
    new_value: Any = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_type": self.transition_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
        }


@dataclass
class SnapshotDiffResult:
    """The complete diff result between two snapshot manifests."""

    changeset_id: str
    workspace_id: str | None
    repository_id: str | None
    from_source_revision: str | None
    to_source_revision: str | None
    from_snapshot_revision: str
    to_snapshot_revision: str
    changed_paths: list[str]
    file_changes: list[FileChange]
    policy_transitions: list[PolicyTransition]
    impacted_entities: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "codecompass.changeset.v1",
            "changeset_id": self.changeset_id,
            "workspace_id": self.workspace_id,
            "repository_id": self.repository_id,
            "from_source_revision": self.from_source_revision,
            "to_source_revision": self.to_source_revision,
            "from_snapshot_revision": self.from_snapshot_revision,
            "to_snapshot_revision": self.to_snapshot_revision,
            "changed_paths": list(self.changed_paths),
            "file_changes": [item.to_dict() for item in self.file_changes],
            "policy_transitions": [item.to_dict() for item in self.policy_transitions],
            "impacted_entities": list(self.impacted_entities),
            "reason_codes": list(self.reason_codes),
        }

    def to_changeset_json(self) -> dict[str, Any]:
        return self.to_dict()


def _compute_changeset_id(from_snapshot_rev: str, to_snapshot_rev: str, sorted_changes: list[dict[str, Any]]) -> str:
    payload = {
        "from": from_snapshot_rev,
        "to": to_snapshot_rev,
        "changes": sorted_changes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_similarity(path1: str, path2: str) -> float:
    left = Path(path1).parts
    right = Path(path2).parts
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    prefix = common / max(len(left), len(right), 1)
    name = 1.0 if Path(path1).name == Path(path2).name else 0.0
    suffix = 1.0 if Path(path1).suffix == Path(path2).suffix else 0.0
    return prefix * 0.6 + name * 0.3 + suffix * 0.1


def _build_file_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("path") or ""): dict(item) for item in list(manifest.get("files") or []) if item.get("path")}


def _build_content_to_paths_index(manifest: dict[str, Any]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for item in list(manifest.get("files") or []):
        digest = str(item.get("content_sha256") or "")
        path = str(item.get("path") or "")
        if digest and path:
            index.setdefault(digest, []).append(path)
    return index


def diff_snapshots(
    old_manifest: dict[str, Any],
    new_manifest: dict[str, Any],
    workspace_id: str | None = None,
    repository_id: str | None = None,
    rename_threshold: float = 0.7,
) -> SnapshotDiffResult:
    if not isinstance(old_manifest, dict):
        raise ValueError("old manifest must be a dictionary")
    if not isinstance(new_manifest, dict):
        raise ValueError("new manifest must be a dictionary")
    if "snapshot_revision" not in old_manifest:
        raise ValueError("old manifest missing 'snapshot_revision'")
    if "snapshot_revision" not in new_manifest:
        raise ValueError("new manifest missing 'snapshot_revision'")
    if "files" not in old_manifest:
        raise ValueError("old manifest missing 'files'")
    if "files" not in new_manifest:
        raise ValueError("new manifest missing 'files'")

    old_index = _build_file_index(old_manifest)
    new_index = _build_file_index(new_manifest)
    old_by_hash = _build_content_to_paths_index(old_manifest)
    changes: list[FileChange] = []
    used_old: set[str] = set()
    used_new: set[str] = set()

    for digest, new_paths in _build_content_to_paths_index(new_manifest).items():
        old_paths = [path for path in old_by_hash.get(digest, []) if path not in used_old]
        for new_path in new_paths:
            if new_path in old_index:
                continue
            unused_old = [path for path in old_paths if path not in new_index]
            if len(unused_old) == 1:
                best, best_score = unused_old[0], 1.0
            else:
                best = None
                best_score = 0.0
                for old_path in unused_old:
                    score = _path_similarity(old_path, new_path)
                    if score > best_score:
                        best, best_score = old_path, score
            if best and best_score >= rename_threshold:
                old_row = old_index[best]
                new_row = new_index[new_path]
                changes.append(
                    FileChange(
                        operation="rename",
                        path=best,
                        new_path=new_path,
                        old_content_sha256=str(old_row.get("content_sha256") or "") or None,
                        new_content_sha256=str(new_row.get("content_sha256") or "") or None,
                        old_byte_size=old_row.get("byte_size"),
                        new_byte_size=new_row.get("byte_size"),
                    )
                )
                used_old.add(best)
                used_new.add(new_path)

    for path, new_row in new_index.items():
        if path in used_new:
            continue
        old_row = old_index.get(path)
        if old_row is None:
            changes.append(
                FileChange(
                    operation="add",
                    path=path,
                    new_content_sha256=str(new_row.get("content_sha256") or "") or None,
                    new_byte_size=new_row.get("byte_size"),
                )
            )
            continue
        used_old.add(path)
        same_hash = old_row.get("content_sha256") == new_row.get("content_sha256")
        meta_changed = (
            old_row.get("outcome") != new_row.get("outcome")
            or old_row.get("support_level") != new_row.get("support_level")
            or old_row.get("extractor_id") != new_row.get("extractor_id")
            or old_row.get("extractor_version") != new_row.get("extractor_version")
        )
        if same_hash and not meta_changed:
            continue
        changes.append(
            FileChange(
                operation="metadata_only" if same_hash else "modify",
                path=path,
                old_content_sha256=str(old_row.get("content_sha256") or "") or None,
                new_content_sha256=str(new_row.get("content_sha256") or "") or None,
                old_byte_size=old_row.get("byte_size"),
                new_byte_size=new_row.get("byte_size"),
                outcome_changed=old_row.get("outcome") != new_row.get("outcome"),
                support_level_changed=old_row.get("support_level") != new_row.get("support_level"),
                extractor_changed=old_row.get("extractor_id") != new_row.get("extractor_id")
                or old_row.get("extractor_version") != new_row.get("extractor_version"),
            )
        )

    for path, old_row in old_index.items():
        if path in used_old or path in new_index:
            continue
        changes.append(
            FileChange(
                operation="delete",
                path=path,
                old_content_sha256=str(old_row.get("content_sha256") or "") or None,
                old_byte_size=old_row.get("byte_size"),
            )
        )

    changes.sort(key=lambda item: (item.operation, item.path, item.new_path or ""))
    policy: list[PolicyTransition] = []
    if old_manifest.get("profile_digest") != new_manifest.get("profile_digest"):
        policy.append(
            PolicyTransition(
                "profile_change",
                old_manifest.get("profile_digest"),
                new_manifest.get("profile_digest"),
                "Build profile configuration changed",
            )
        )
    if old_manifest.get("registry_digest") != new_manifest.get("registry_digest"):
        policy.append(
            PolicyTransition(
                "registry_change",
                old_manifest.get("registry_digest"),
                new_manifest.get("registry_digest"),
                "Parser/extractor registry changed",
            )
        )
    if old_manifest.get("pipeline") != new_manifest.get("pipeline"):
        policy.append(
            PolicyTransition(
                "pipeline_change",
                old_manifest.get("pipeline"),
                new_manifest.get("pipeline"),
                f"Pipeline changed from {old_manifest.get('pipeline')} to {new_manifest.get('pipeline')}",
            )
        )

    changed_paths = sorted(
        {
            item.new_path or item.path
            for item in changes
        }
    )
    changeset_id = _compute_changeset_id(
        str(old_manifest["snapshot_revision"]),
        str(new_manifest["snapshot_revision"]),
        [item.to_dict() for item in changes],
    )
    reasons = [item.operation for item in changes] + [item.transition_type for item in policy]
    return SnapshotDiffResult(
        changeset_id=changeset_id,
        workspace_id=workspace_id,
        repository_id=repository_id,
        from_source_revision=old_manifest.get("source_revision"),
        to_source_revision=new_manifest.get("source_revision"),
        from_snapshot_revision=str(old_manifest["snapshot_revision"]),
        to_snapshot_revision=str(new_manifest["snapshot_revision"]),
        changed_paths=changed_paths,
        file_changes=changes,
        policy_transitions=policy,
        impacted_entities=list(changed_paths),
        reason_codes=sorted(set(reasons)),
    )


def compute_changeset_from_manifest_files(
    old_manifest_path: str | Path,
    new_manifest_path: str | Path,
    workspace_id: str | None = None,
    repository_id: str | None = None,
) -> SnapshotDiffResult:
    old_manifest = json.loads(Path(old_manifest_path).read_text(encoding="utf-8"))
    new_manifest = json.loads(Path(new_manifest_path).read_text(encoding="utf-8"))
    return diff_snapshots(old_manifest, new_manifest, workspace_id, repository_id)

"""CodeCompass Snapshot Diff Service — computes canonical ChangeSets from
two repository snapshot manifests.

This module implements CIL-005: Snapshot-Diff auf Basis bestehender Datei-Hashes.
It compares two codecompass_snapshot_manifest.v1 instances and produces a
canonical ChangeSet with add/modify/delete/rename/policy_transition operations.

Key design decisions:
- Reuses existing codecompass_snapshot_manifest.v1 schema
- Deterministic sorting for stable ChangeSet-ID generation
- Rename detection only when content_sha256 matches AND path similarity is high
- Falls back to delete+add when rename confidence is low
- Policy transitions detected via profile_digest changes
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
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
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "operation": self.operation,
            "path": self.path,
        }
        if self.new_path:
            result["new_path"] = self.new_path
        if self.old_content_sha256:
            result["old_content_sha256"] = self.old_content_sha256
        if self.new_content_sha256:
            result["new_content_sha256"] = self.new_content_sha256
        if self.old_byte_size is not None:
            result["old_byte_size"] = self.old_byte_size
        if self.new_byte_size is not None:
            result["new_byte_size"] = self.new_byte_size
        if self.outcome_changed:
            result["outcome_changed"] = True
        if self.support_level_changed:
            result["support_level_changed"] = True
        if self.extractor_changed:
            result["extractor_changed"] = True
        return result


@dataclass(frozen=True)
class PolicyTransition:
    """Detected policy/configuration transition between snapshots."""
    transition_type: Literal[
        "profile_change",
        "registry_change", 
        "pipeline_change",
        "budget_change",
        "required_paths_change"
    ]
    old_value: Any = None
    new_value: Any = None
    description: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "transition_type": self.transition_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
        }


@dataclass(frozen=True)
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
        """Convert to dictionary for JSON serialization."""
        return {
            "changeset_id": self.changeset_id,
            "workspace_id": self.workspace_id,
            "repository_id": self.repository_id,
            "from_source_revision": self.from_source_revision,
            "to_source_revision": self.to_source_revision,
            "from_snapshot_revision": self.from_snapshot_revision,
            "to_snapshot_revision": self.to_snapshot_revision,
            "changed_paths": self.changed_paths,
            "file_changes": [fc.to_dict() for fc in self.file_changes],
            "policy_transitions": [pt.to_dict() for pt in self.policy_transitions],
            "impacted_entities": self.impacted_entities,
            "reason_codes": self.reason_codes,
        }
    
    def to_changeset_json(self) -> str:
        """Serialize as canonical JSON for ChangeSet storage."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _compute_changeset_id(
    from_snapshot_rev: str,
    to_snapshot_rev: str,
    sorted_changes: list[dict[str, Any]],
) -> str:
    """Compute deterministic ChangeSet-ID from diff content.
    
    Uses SHA256 over canonical JSON representation to ensure:
    - Same manifest pairs always produce same ChangeSet-ID
    - Content-addressable for deduplication
    """
    canonical_data = {
        "from_snapshot_revision": from_snapshot_rev,
        "to_snapshot_revision": to_snapshot_rev,
        "changes": sorted_changes,
    }
    canonical_json = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _path_similarity(path1: str, path2: str) -> float:
    """Compute similarity score between two file paths (0.0 to 1.0).
    
    Uses a simple heuristic based on:
    - Common prefix length
    - Filename matching
    - Extension matching
    """
    if path1 == path2:
        return 1.0
    
    p1_parts = Path(path1).parts
    p2_parts = Path(path2).parts
    
    # Count common prefix parts
    common_prefix = 0
    for a, b in zip(p1_parts, p2_parts):
        if a == b:
            common_prefix += 1
        else:
            break
    
    # Normalize by total length
    max_len = max(len(p1_parts), len(p2_parts))
    if max_len == 0:
        return 0.0
    
    prefix_score = common_prefix / max_len
    
    # Check filename match
    filename_match = 1.0 if Path(path1).name == Path(path2).name else 0.0
    
    # Check extension match
    ext_match = 1.0 if Path(path1).suffix == Path(path2).suffix else 0.0
    
    # Weighted average
    return (prefix_score * 0.5) + (filename_match * 0.3) + (ext_match * 0.2)


def _build_file_index(
    manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Build index of files by path for O(1) lookups."""
    return {
        file_entry["path"]: file_entry
        for file_entry in manifest.get("files", [])
    }


def _build_content_to_paths_index(
    manifest: dict[str, Any]
) -> dict[str, list[str]]:
    """Build reverse index from content_sha256 to paths for rename detection."""
    index: dict[str, list[str]] = {}
    for file_entry in manifest.get("files", []):
        sha = file_entry.get("content_sha256")
        if sha:
            if sha not in index:
                index[sha] = []
            index[sha].append(file_entry["path"])
    return index


def diff_snapshots(
    old_manifest: dict[str, Any],
    new_manifest: dict[str, Any],
    workspace_id: str | None = None,
    repository_id: str | None = None,
    rename_threshold: float = 0.7,
) -> SnapshotDiffResult:
    """Compute canonical ChangeSet between two CodeCompass snapshot manifests.
    
    Args:
        old_manifest: Previous snapshot manifest (codecompass_snapshot_manifest.v1)
        new_manifest: Current snapshot manifest (codecompass_snapshot_manifest.v1)
        workspace_id: Optional workspace identifier
        repository_id: Optional repository identifier  
        rename_threshold: Minimum path similarity for rename detection (default 0.7)
    
    Returns:
        SnapshotDiffResult with canonical ChangeSet-ID and classified operations
    
    Raises:
        ValueError: If manifests are invalid or missing required fields
    """
    # Validate required fields
    for manifest, label in [(old_manifest, "old"), (new_manifest, "new")]:
        if not isinstance(manifest, dict):
            raise ValueError(f"{label} manifest must be a dictionary")
        if "snapshot_revision" not in manifest:
            raise ValueError(f"{label} manifest missing 'snapshot_revision'")
        if "files" not in manifest:
            raise ValueError(f"{label} manifest missing 'files'")
    
    from_snapshot_rev = old_manifest["snapshot_revision"]
    to_snapshot_rev = new_manifest["snapshot_revision"]
    from_source_rev = old_manifest.get("source_revision")
    to_source_rev = new_manifest.get("source_revision")
    
    # Build indexes
    old_files = _build_file_index(old_manifest)
    new_files = _build_file_index(new_manifest)
    old_content_index = _build_content_to_paths_index(old_manifest)
    new_content_index = _build_content_to_paths_index(new_manifest)
    
    file_changes: list[FileChange] = []
    processed_old_paths: set[str] = set()
    processed_new_paths: set[str] = set()
    
    # Detect renames first (content hash match + path similarity)
    for new_path, new_entry in new_files.items():
        new_sha = new_entry.get("content_sha256")
        if not new_sha or new_path in processed_new_paths:
            continue
        
        # Look for potential rename candidates in old manifest
        old_candidates = old_content_index.get(new_sha, [])
        best_rename: tuple[str, float] | None = None
        
        for old_path in old_candidates:
            if old_path in processed_old_paths:
                continue
            if old_path == new_path:
                continue  # Same path = not a rename
            
            similarity = _path_similarity(old_path, new_path)
            if similarity >= rename_threshold:
                if best_rename is None or similarity > best_rename[1]:
                    best_rename = (old_path, similarity)
        
        if best_rename:
            old_path, _ = best_rename
            old_entry = old_files[old_path]
            
            # Check for metadata changes
            outcome_changed = old_entry.get("outcome") != new_entry.get("outcome")
            support_changed = old_entry.get("support_level") != new_entry.get("support_level")
            extractor_changed = (
                old_entry.get("extractor_id") != new_entry.get("extractor_id") or
                old_entry.get("extractor_version") != new_entry.get("extractor_version")
            )
            
            file_changes.append(FileChange(
                operation="rename",
                path=old_path,
                new_path=new_path,
                old_content_sha256=new_sha,
                new_content_sha256=new_sha,
                old_byte_size=old_entry.get("byte_size"),
                new_byte_size=new_entry.get("byte_size"),
                outcome_changed=outcome_changed,
                support_level_changed=support_changed,
                extractor_changed=extractor_changed,
            ))
            processed_old_paths.add(old_path)
            processed_new_paths.add(new_path)
    
    # Detect adds, modifies, deletes
    all_paths = set(old_files.keys()) | set(new_files.keys())
    
    for path in sorted(all_paths):
        if path in processed_old_paths or path in processed_new_paths:
            continue
        
        old_entry = old_files.get(path)
        new_entry = new_files.get(path)
        
        if old_entry is None and new_entry is not None:
            # ADD
            file_changes.append(FileChange(
                operation="add",
                path=path,
                new_content_sha256=new_entry.get("content_sha256"),
                new_byte_size=new_entry.get("byte_size"),
            ))
            processed_new_paths.add(path)
            
        elif old_entry is not None and new_entry is None:
            # DELETE
            file_changes.append(FileChange(
                operation="delete",
                path=path,
                old_content_sha256=old_entry.get("content_sha256"),
                old_byte_size=old_entry.get("byte_size"),
            ))
            processed_old_paths.add(path)
            
        elif old_entry is not None and new_entry is not None:
            # Check for modifications
            old_sha = old_entry.get("content_sha256")
            new_sha = new_entry.get("content_sha256")
            
            if old_sha != new_sha:
                # Content MODIFY
                file_changes.append(FileChange(
                    operation="modify",
                    path=path,
                    old_content_sha256=old_sha,
                    new_content_sha256=new_sha,
                    old_byte_size=old_entry.get("byte_size"),
                    new_byte_size=new_entry.get("byte_size"),
                    outcome_changed=old_entry.get("outcome") != new_entry.get("outcome"),
                    support_level_changed=old_entry.get("support_level") != new_entry.get("support_level"),
                    extractor_changed=(
                        old_entry.get("extractor_id") != new_entry.get("extractor_id") or
                        old_entry.get("extractor_version") != new_entry.get("extractor_version")
                    ),
                ))
            else:
                # Check for metadata-only changes
                outcome_changed = old_entry.get("outcome") != new_entry.get("outcome")
                support_changed = old_entry.get("support_level") != new_entry.get("support_level")
                extractor_changed = (
                    old_entry.get("extractor_id") != new_entry.get("extractor_id") or
                    old_entry.get("extractor_version") != new_entry.get("extractor_version")
                )
                
                if outcome_changed or support_changed or extractor_changed:
                    file_changes.append(FileChange(
                        operation="metadata_only",
                        path=path,
                        old_content_sha256=old_sha,
                        new_content_sha256=new_sha,
                        outcome_changed=outcome_changed,
                        support_level_changed=support_changed,
                        extractor_changed=extractor_changed,
                    ))
            
            processed_old_paths.add(path)
            processed_new_paths.add(path)
    
    # Detect policy transitions
    policy_transitions: list[PolicyTransition] = []
    
    # Profile change
    old_profile_digest = old_manifest.get("profile_digest")
    new_profile_digest = new_manifest.get("profile_digest")
    if old_profile_digest != new_profile_digest:
        policy_transitions.append(PolicyTransition(
            transition_type="profile_change",
            old_value=old_profile_digest,
            new_value=new_profile_digest,
            description="Build profile configuration changed",
        ))
    
    # Registry change
    old_registry_digest = old_manifest.get("registry_digest")
    new_registry_digest = new_manifest.get("registry_digest")
    if old_registry_digest != new_registry_digest:
        policy_transitions.append(PolicyTransition(
            transition_type="registry_change",
            old_value=old_registry_digest,
            new_value=new_registry_digest,
            description="Parser/extractor registry changed",
        ))
    
    # Pipeline change
    old_pipeline = old_manifest.get("pipeline")
    new_pipeline = new_manifest.get("pipeline")
    if old_pipeline != new_pipeline:
        policy_transitions.append(PolicyTransition(
            transition_type="pipeline_change",
            old_value=old_pipeline,
            new_value=new_pipeline,
            description=f"Pipeline changed from {old_pipeline} to {new_pipeline}",
        ))
    
    # Required paths change
    old_required = old_manifest.get("required_paths", {})
    new_required = new_manifest.get("required_paths", {})
    if old_required != new_required:
        policy_transitions.append(PolicyTransition(
            transition_type="required_paths_change",
            old_value=old_required,
            new_value=new_required,
            description="Required path rules changed",
        ))
    
    # Compute reason codes
    reason_codes: list[str] = []
    if any(fc.operation == "add" for fc in file_changes):
        reason_codes.append("files_added")
    if any(fc.operation == "delete" for fc in file_changes):
        reason_codes.append("files_deleted")
    if any(fc.operation == "modify" for fc in file_changes):
        reason_codes.append("files_modified")
    if any(fc.operation == "rename" for fc in file_changes):
        reason_codes.append("files_renamed")
    if policy_transitions:
        reason_codes.append("policy_transition")
    if not file_changes and not policy_transitions:
        reason_codes.append("no_changes")
    
    # Build changed paths list
    changed_paths = sorted(set(
        path for fc in file_changes 
        for path in ([fc.path, fc.new_path] if fc.new_path else [fc.path])
    ))
    
    # Compute impacted entities (simplified: affected directories and parent paths)
    impacted_entities: list[str] = []
    for path in changed_paths:
        parts = Path(path).parts
        for i in range(1, len(parts) + 1):
            entity = "/".join(parts[:i])
            if entity not in impacted_entities:
                impacted_entities.append(entity)
    impacted_entities.sort()
    
    # Sort file changes for deterministic output
    sorted_changes = sorted(
        [fc.to_dict() for fc in file_changes],
        key=lambda x: (x["operation"], x["path"]),
    )
    
    # Compute canonical ChangeSet-ID
    changeset_id = _compute_changeset_id(
        from_snapshot_rev,
        to_snapshot_rev,
        sorted_changes,
    )
    
    return SnapshotDiffResult(
        changeset_id=changeset_id,
        workspace_id=workspace_id,
        repository_id=repository_id,
        from_source_revision=from_source_rev,
        to_source_revision=to_source_rev,
        from_snapshot_revision=from_snapshot_rev,
        to_snapshot_revision=to_snapshot_rev,
        changed_paths=changed_paths,
        file_changes=file_changes,
        policy_transitions=policy_transitions,
        impacted_entities=impacted_entities,
        reason_codes=reason_codes,
    )


def compute_changeset_from_manifest_files(
    old_manifest_path: Path,
    new_manifest_path: Path,
    workspace_id: str | None = None,
    repository_id: str | None = None,
) -> SnapshotDiffResult:
    """Load two manifest files and compute their ChangeSet.
    
    Convenience function for CLI/API usage.
    """
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    new_manifest = json.loads(new_manifest_path.read_text(encoding="utf-8"))
    
    return diff_snapshots(
        old_manifest,
        new_manifest,
        workspace_id=workspace_id,
        repository_id=repository_id,
    )

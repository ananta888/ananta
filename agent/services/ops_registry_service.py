from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorkspaceRef:
    workspace_id: str
    root: Path


@dataclass(frozen=True)
class ComposeProjectRef:
    project_id: str
    name: str
    project_directory: Path
    compose_files: tuple[Path, ...]
    profiles: tuple[str, ...]
    marker: str
    category: str
    allowed_actions: tuple[str, ...]


class OpsRegistryService:
    """Registry for workspace-scoped Ops access.

    Clients provide IDs, never raw host paths. The default registry exposes the
    current repository as ``repo`` for local operator diagnostics.
    """

    def __init__(self, *, repo_root: Path | None = None, workspaces: Iterable[WorkspaceRef] | None = None) -> None:
        self._repo_root = Path(repo_root or Path.cwd()).resolve()
        refs = list(workspaces or [])
        if not refs:
            refs = [WorkspaceRef("repo", self._repo_root)]
        self._workspaces = {ref.workspace_id: WorkspaceRef(ref.workspace_id, ref.root.resolve()) for ref in refs}

    def resolve_workspace(self, workspace_id: str | None) -> WorkspaceRef | None:
        key = str(workspace_id or "repo").strip() or "repo"
        return self._workspaces.get(key)

    def resolve_relative_path(self, workspace_id: str | None, path: str | None) -> Path | None:
        workspace = self.resolve_workspace(workspace_id)
        if workspace is None:
            return None
        rel = str(path or "").strip()
        if not rel:
            return workspace.root
        candidate = (workspace.root / rel).resolve()
        try:
            candidate.relative_to(workspace.root)
        except ValueError:
            return None
        return candidate

    def compose_projects(self) -> list[ComposeProjectRef]:
        projects: list[ComposeProjectRef] = []
        self._add_compose_dir(projects, self._repo_root / "docker" / "compose-next", marker="preferred")
        self._add_compose_dir(projects, self._repo_root / "docker" / "old_way", marker="legacy")
        return projects

    def resolve_compose_project(self, project_id: str) -> ComposeProjectRef | None:
        wanted = str(project_id or "").strip()
        return next((project for project in self.compose_projects() if project.project_id == wanted), None)

    def _add_compose_dir(self, projects: list[ComposeProjectRef], directory: Path, *, marker: str) -> None:
        if not directory.exists():
            return
        patterns = ("compose*.yml", "compose*.yaml", "docker-compose*.yml", "docker-compose*.yaml")
        seen: set[Path] = set()
        for pattern in patterns:
            seen.update(path.resolve() for path in directory.glob(pattern) if path.is_file())
        for compose_file in sorted(seen):
            stem = compose_file.stem.replace("docker-compose", "compose").replace("compose.", "")
            category = self._category_for(compose_file.name)
            project_id = self._project_id(marker, compose_file)
            projects.append(
                ComposeProjectRef(
                    project_id=project_id,
                    name=stem or compose_file.stem,
                    project_directory=directory.resolve(),
                    compose_files=(compose_file,),
                    profiles=self._profiles_for(compose_file.name),
                    marker=marker,
                    category=category,
                    allowed_actions=("status", "config", "logs"),
                )
            )

    def _project_id(self, marker: str, compose_file: Path) -> str:
        rel = str(compose_file.relative_to(self._repo_root)) if compose_file.is_relative_to(self._repo_root) else str(compose_file)
        digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
        return f"{marker}-{compose_file.stem.replace('.', '-')}-{digest}"

    @staticmethod
    def _profiles_for(name: str) -> tuple[str, ...]:
        lower = name.lower()
        profiles = []
        for item in ("prod", "dev", "lite", "oidc", "ci", "e2e", "public-rendezvous", "tests"):
            if item in lower:
                profiles.append(item)
        return tuple(profiles)

    @staticmethod
    def _category_for(name: str) -> str:
        lower = name.lower()
        if "public-rendezvous" in lower:
            return "public-rendezvous"
        if "oidc" in lower:
            return "oidc"
        if "e2e" in lower:
            return "e2e"
        if "ci" in lower or "test" in lower:
            return "ci"
        if "lite" in lower:
            return "lite"
        if "dev" in lower:
            return "dev"
        return "prod" if "stack" in lower or "full" in lower else "dev"


_default_registry: OpsRegistryService | None = None


def get_ops_registry_service() -> OpsRegistryService:
    global _default_registry
    if _default_registry is None:
        _default_registry = OpsRegistryService()
    return _default_registry

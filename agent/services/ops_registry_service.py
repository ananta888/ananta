from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from flask import current_app, has_app_context


@dataclass(frozen=True)
class WorkspaceRef:
    workspace_id: str
    root: Path
    label: str = ""
    source: str = "configured"


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
    project_name: str = ""
    available_profiles: tuple[str, ...] = ()
    env_files: tuple[Path, ...] = ()


class OpsRegistryService:
    """Registry for workspace-scoped Ops access.

    Clients provide IDs, never raw host paths. The default registry exposes the
    current repository as ``repo`` for local operator diagnostics.
    """

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        workspaces: Iterable[WorkspaceRef] | None = None,
        compose_projects: Iterable[ComposeProjectRef] | None = None,
    ) -> None:
        self._repo_root = Path(repo_root or Path.cwd()).resolve()
        refs = list(workspaces or [])
        if not refs:
            refs = [WorkspaceRef("repo", self._repo_root)]
        self._workspaces = {
            ref.workspace_id: WorkspaceRef(ref.workspace_id, ref.root.resolve(), ref.label, ref.source) for ref in refs
        }
        self._configured_compose_projects = tuple(compose_projects or ())
        self._compose_discovery_cache: tuple[float, tuple[ComposeProjectRef, ...]] | None = None

    def resolve_workspace(self, workspace_id: str | None) -> WorkspaceRef | None:
        key = str(workspace_id or "repo").strip() or "repo"
        return self._workspace_map().get(key)

    def workspaces(self) -> list[WorkspaceRef]:
        """Return server-registered and locally discovered Git workspaces.

        Discovery is restricted to direct children of ANANTA_WORKSPACE_ROOT.
        Clients still address entries by opaque IDs and never submit paths.
        """

        refs = self._workspace_map()
        return sorted(refs.values(), key=lambda item: (item.workspace_id != "repo", item.label or item.workspace_id))

    def _workspace_map(self) -> dict[str, WorkspaceRef]:
        refs = dict(self._workspaces)
        for ref in self._configured_git_workspaces():
            refs.setdefault(ref.workspace_id, ref)
        root_value = str(os.environ.get("ANANTA_WORKSPACE_ROOT") or "").strip()
        if root_value:
            root = Path(root_value).resolve()
            if root.is_dir():
                for child in sorted(root.iterdir()):
                    resolved = child.resolve()
                    if not child.is_dir() or not (resolved / ".git").exists():
                        continue
                    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
                    workspace_id = f"workspace-{self._safe_id(child.name)}-{digest}"
                    refs.setdefault(
                        workspace_id,
                        WorkspaceRef(workspace_id, resolved, child.name, "project-workspaces"),
                    )
        return refs

    def _configured_git_workspaces(self) -> list[WorkspaceRef]:
        if not has_app_context():
            return []
        agent_config = current_app.config.get("AGENT_CONFIG", {}) or {}
        git_config = dict(agent_config.get("git_ops") or {})
        raw_items = git_config.get("workspaces") or []
        if isinstance(raw_items, dict):
            raw_items = [
                {"workspace_id": workspace_id, **(value if isinstance(value, dict) else {"root": value})}
                for workspace_id, value in raw_items.items()
            ]
        refs: list[WorkspaceRef] = []
        for raw in list(raw_items or []):
            if not isinstance(raw, dict):
                continue
            workspace_id = str(raw.get("workspace_id") or raw.get("id") or "").strip()
            root_value = str(raw.get("root") or "").strip()
            if not workspace_id or not root_value:
                continue
            root = Path(root_value).expanduser().resolve()
            if not root.is_dir():
                continue
            refs.append(
                WorkspaceRef(
                    workspace_id=workspace_id,
                    root=root,
                    label=str(raw.get("label") or workspace_id),
                    source="git_ops_config",
                )
            )
        return refs

    @staticmethod
    def _safe_id(value: str) -> str:
        cleaned = "".join(char.lower() if char.isalnum() else "-" for char in str(value or ""))
        return "-".join(part for part in cleaned.split("-") if part)[:48] or "repo"

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
        if self._configured_compose_projects:
            return list(self._configured_compose_projects)
        docker_ops = self._docker_ops_config()
        if "compose_projects" in docker_ops:
            # An explicit registry is authoritative even when empty/invalid;
            # never broaden access by silently falling back to discovery.
            return self._projects_from_config(docker_ops)
        now = time.monotonic()
        cached = self._compose_discovery_cache
        if cached is not None and cached[0] > now:
            return list(cached[1])
        projects: list[ComposeProjectRef] = []
        self._add_compose_dir(projects, self._repo_root / "docker" / "compose-next", marker="preferred")
        self._add_compose_dir(projects, self._repo_root / "docker" / "old_way", marker="legacy")
        self._add_known_multi_file_projects(projects)
        self._compose_discovery_cache = (now + 2.0, tuple(projects))
        return projects

    def resolve_compose_project(self, project_id: str) -> ComposeProjectRef | None:
        wanted = str(project_id or "").strip()
        return next((project for project in self.compose_projects() if project.project_id == wanted), None)

    def container_allowed_actions(
        self,
        *,
        container_id: str,
        name: str,
        compose_project: str = "",
    ) -> tuple[str, ...]:
        """Return actions for an explicitly managed container registration.

        Container discovery remains readable after the Docker boundary is enabled,
        but mutations require either a matching ``docker_ops.managed_containers``
        entry or membership in a server-registered mutable Compose project. This
        prevents an arbitrary client-provided container name from becoming a
        control target merely because the Docker daemon can resolve it.
        """

        registrations = list(self._docker_ops_config().get("managed_containers") or [])
        registrations.extend(
            item.strip()
            for item in str(os.environ.get("ANANTA_DOCKER_OPS_MANAGED_CONTAINERS") or "").split(",")
            if item.strip()
        )
        safe_actions = {"logs", "inspect_light", "stats", "start", "stop", "restart"}
        for registration in registrations:
            if isinstance(registration, str):
                matches = registration in {container_id, name, "*"}
                actions = safe_actions
            elif isinstance(registration, dict):
                wanted_id = str(registration.get("container_id") or registration.get("id") or "").strip()
                wanted_name = str(registration.get("name") or "").strip()
                wanted_project = str(registration.get("compose_project") or "").strip()
                matches = (
                    (not wanted_id or wanted_id in {container_id, "*"})
                    and (not wanted_name or wanted_name in {name, "*"})
                    and (not wanted_project or wanted_project in {compose_project, "*"})
                    and bool(wanted_id or wanted_name or wanted_project)
                )
                configured_actions = registration.get("allowed_actions")
                actions = set(configured_actions or safe_actions) & safe_actions
            else:
                continue
            if matches:
                return tuple(sorted(actions))
        if compose_project:
            # Containers belonging to a server-registered mutable Compose
            # project inherit only the corresponding safe lifecycle actions.
            matching_actions = {
                action
                for project in self.compose_projects()
                if project.project_name == compose_project
                for action in project.allowed_actions
            }
            if matching_actions:
                inherited = {"logs", "inspect_light", "stats"}
                if "up" in matching_actions:
                    inherited.add("start")
                if "stop" in matching_actions or "down" in matching_actions:
                    inherited.add("stop")
                if "restart" in matching_actions:
                    inherited.add("restart")
                return tuple(sorted(inherited))
        return ()

    def _add_compose_dir(self, projects: list[ComposeProjectRef], directory: Path, *, marker: str) -> None:
        if not directory.exists():
            return
        patterns = ("compose*.yml", "compose*.yaml", "docker-compose*.yml", "docker-compose*.yaml")
        seen: set[Path] = set()
        for pattern in patterns:
            seen.update(path.resolve() for path in directory.glob(pattern) if path.is_file())
        for compose_file in sorted(seen):
            if marker == "preferred" and compose_file.name in {
                "compose.base.yml",
                "compose.tests-backend-base.yml",
                "compose.voice-restricted.yml",
            }:
                # Shared bases and overlays are represented only through their
                # runnable multi-file project definitions.
                continue
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
                    allowed_actions=self._default_compose_actions(marker, category=category),
                    project_name=directory.name,
                    available_profiles=self._profiles_for(compose_file.name),
                    env_files=self._default_compose_env_files(),
                )
            )

    def _add_known_multi_file_projects(self, projects: list[ComposeProjectRef]) -> None:
        directory = (self._repo_root / "docker" / "compose-next").resolve()
        full = directory / "compose.stack.full.yml"
        voice = directory / "compose.voice-restricted.yml"
        if not full.is_file() or not voice.is_file():
            return
        for profile in ("voice-production-minimal", "voice-production-cpu", "voice-production-nvidia"):
            files = (full.resolve(), voice.resolve())
            projects.append(
                ComposeProjectRef(
                    project_id=self._project_id("preferred", *files, discriminator=profile),
                    name=f"full + voice ({profile})",
                    project_directory=directory,
                    compose_files=files,
                    profiles=(profile,),
                    marker="preferred",
                    category="prod",
                    allowed_actions=self._default_compose_actions("preferred"),
                    project_name=directory.name,
                    available_profiles=(
                        "voice-production-minimal",
                        "voice-production-cpu",
                        "voice-production-nvidia",
                    ),
                    env_files=self._default_compose_env_files(),
                )
            )

    def _projects_from_config(self, docker_ops: dict[str, Any] | None = None) -> list[ComposeProjectRef]:
        raw_projects = list((docker_ops or self._docker_ops_config()).get("compose_projects") or [])
        projects: list[ComposeProjectRef] = []
        seen_ids: set[str] = set()
        for raw in raw_projects:
            if not isinstance(raw, dict):
                continue
            directory = self._safe_repo_path(raw.get("project_directory") or raw.get("directory") or ".")
            file_values = raw.get("compose_files") or raw.get("files") or []
            if isinstance(file_values, str):
                file_values = [file_values]
            compose_files = tuple(filter(None, (self._safe_repo_file(value) for value in file_values)))
            if directory is None or not compose_files:
                continue
            env_values = raw.get("env_files") or []
            if isinstance(env_values, str):
                env_values = [env_values]
            env_files = tuple(filter(None, (self._safe_repo_file(value) for value in env_values)))
            profiles = self._strings(raw.get("profiles"))
            available_profiles = self._strings(raw.get("available_profiles")) or profiles
            if any(not self._valid_compose_name(profile) for profile in (*profiles, *available_profiles)):
                continue
            marker = str(raw.get("marker") or "preferred").strip() or "preferred"
            allowed = tuple(
                action
                for action in self._strings(raw.get("allowed_actions")) or self._default_compose_actions(marker)
                if action in {"status", "config", "logs", "pull", "up", "stop", "restart", "down"}
            )
            explicit_id = str(raw.get("project_id") or raw.get("id") or "").strip()
            if explicit_id and not self._valid_registry_id(explicit_id):
                continue
            project_id = explicit_id or self._project_id(marker, *compose_files, discriminator=",".join(profiles))
            if project_id in seen_ids:
                continue
            seen_ids.add(project_id)
            project_name = str(raw.get("project_name") or directory.name).strip()
            if not self._valid_compose_name(project_name):
                continue
            projects.append(
                ComposeProjectRef(
                    project_id=project_id,
                    name=str(raw.get("name") or project_id),
                    project_directory=directory,
                    compose_files=compose_files,
                    profiles=profiles,
                    marker=marker,
                    category=str(raw.get("category") or "dev"),
                    allowed_actions=allowed,
                    project_name=project_name,
                    available_profiles=available_profiles,
                    env_files=env_files,
                )
            )
        return projects

    def _safe_repo_path(self, value: Any) -> Path | None:
        candidate = (self._repo_root / str(value or ".")).resolve()
        try:
            candidate.relative_to(self._repo_root)
        except ValueError:
            return None
        return candidate if candidate.is_dir() else None

    def _safe_repo_file(self, value: Any) -> Path | None:
        candidate = (self._repo_root / str(value or "")).resolve()
        try:
            candidate.relative_to(self._repo_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _strings(value: Any) -> tuple[str, ...]:
        values = [value] if isinstance(value, str) else list(value or [])
        return tuple(str(item).strip() for item in values if str(item).strip())

    @staticmethod
    def _valid_registry_id(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(value or "")))

    @staticmethod
    def _valid_compose_name(value: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(value or "")))

    @staticmethod
    def _default_compose_actions(marker: str, *, category: str = "") -> tuple[str, ...]:
        read = ("status", "config", "logs")
        if marker != "preferred" or category in {"ci", "e2e", "tests"}:
            return read
        return (*read, "pull", "up", "stop", "restart", "down")

    def _docker_ops_config(self) -> dict[str, Any]:
        if not has_app_context():
            return {}
        agent_config = current_app.config.get("AGENT_CONFIG", {}) or {}
        return dict(agent_config.get("docker_ops") or {})

    def _default_compose_env_files(self) -> tuple[Path, ...]:
        explicit = str(os.environ.get("ANANTA_DOCKER_OPS_ENV_FILE") or "").strip()
        if explicit:
            candidate = Path(explicit).expanduser().resolve()
            if candidate.is_file():
                return (candidate,)
        repo_env = (self._repo_root / ".env").resolve()
        return (repo_env,) if repo_env.is_file() else ()

    def _project_id(self, marker: str, *compose_files: Path, discriminator: str = "") -> str:
        relative = [
            str(path.relative_to(self._repo_root)) if path.is_relative_to(self._repo_root) else str(path)
            for path in compose_files
        ]
        digest_input = "\0".join([*relative, discriminator])
        digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:8]
        stem = compose_files[0].stem.replace(".", "-") if compose_files else "project"
        return f"{marker}-{stem}-{digest}"

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

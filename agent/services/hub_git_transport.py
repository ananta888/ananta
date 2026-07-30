"""Bounded, temporary Hub-side Git transport for source connectors."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Protocol

from agent.services.git_remote_policy_service import (
    GitRemotePolicyError,
    GitTransportAuthorization,
    hardened_git_network_args,
    hardened_git_transport_args,
)
from agent.services.hub_git_credential_resolver import (
    AuthorizedGitCommandSessionPort,
    GitCommandResult,
    GitCredentialCommandResolverPort,
    HubGitCredentialError,
)
from agent.sources.git_source_connector_common import (
    GitConnectorProviderError,
    GitContentRequest,
    GitRepositoryBudgets,
    GitRepositoryMetrics,
)


_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_FULL_REF = re.compile(r"^refs/[A-Za-z0-9._/-]{1,240}$")


class HubGitTransportPort(Protocol):
    def supports_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def resolve_commit(
        self,
        *,
        authorization: GitTransportAuthorization,
        credential_username: str | None,
        requested_ref: str,
    ) -> str: ...

    def inspect_content(
        self,
        request: GitContentRequest,
        *,
        credential_username: str | None,
    ) -> GitRepositoryMetrics: ...


class SecureTemporaryGitWorkspace:
    def __init__(self, *, root: Path) -> None:
        self._root = Path(root)

    @contextmanager
    def open(self) -> Iterator[Path]:
        if self._root.is_symlink():
            raise GitConnectorProviderError(
                "git_temporary_workspace_root_invalid"
            )
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self._root.is_dir() or self._root.is_symlink():
            raise GitConnectorProviderError(
                "git_temporary_workspace_root_invalid"
            )
        workspace = Path(
            tempfile.mkdtemp(prefix="ananta-git-", dir=str(self._root))
        )
        try:
            yield workspace
        finally:
            self._make_writable(workspace)
            shutil.rmtree(workspace, ignore_errors=True)
            if workspace.exists():
                raise GitConnectorProviderError(
                    "git_temporary_workspace_cleanup_failed"
                )

    @staticmethod
    def _make_writable(root: Path) -> None:
        if not root.exists():
            return
        for current, directories, files in os.walk(
            root,
            topdown=False,
            followlinks=False,
        ):
            for name in files:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
                    except OSError:
                        pass
            for name in directories:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        path.chmod(0o700)
                    except OSError:
                        pass
        try:
            root.chmod(0o700)
        except OSError:
            pass


class HubGitTransport(HubGitTransportPort):
    def __init__(
        self,
        *,
        credential_resolver: GitCredentialCommandResolverPort,
        workspace_root: Path,
        monotonic=time.monotonic,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._workspaces = SecureTemporaryGitWorkspace(root=workspace_root)
        self._monotonic = monotonic

    def supports_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        try:
            authorization.validate()
        except GitRemotePolicyError:
            return False
        return authorization.scheme in {"https", "ssh"}

    def resolve_commit(
        self,
        *,
        authorization: GitTransportAuthorization,
        credential_username: str | None,
        requested_ref: str,
    ) -> str:
        self._validate_authorization(authorization)
        normalized_ref = str(requested_ref or "").strip()
        if _COMMIT_SHA.fullmatch(normalized_ref):
            return normalized_ref
        candidates = self._ref_candidates(normalized_ref)
        try:
            with self._credential_resolver.open_session(
                credential_ref=authorization.credential_ref,
                credential_username=credential_username,
                scheme=authorization.scheme,
            ) as session:
                with self._workspaces.open() as workspace:
                    for candidate in candidates:
                        result = session.run(
                            hardened_git_transport_args(
                                authorization,
                                [
                                    "ls-remote",
                                    "--exit-code",
                                    authorization.canonical_url,
                                    candidate,
                                ],
                            ),
                            cwd=workspace,
                            timeout_seconds=30,
                        )
                        if result.returncode == 2:
                            continue
                        self._require_result(
                            result,
                            "git_commit_resolution_failed",
                        )
                        commit = self._parse_ls_remote(
                            result.stdout,
                            candidate,
                        )
                        if commit is not None:
                            return commit
        except HubGitCredentialError as exc:
            raise GitConnectorProviderError(exc.reason_code) from None
        raise GitConnectorProviderError("git_ref_not_found")

    def inspect_content(
        self,
        request: GitContentRequest,
        *,
        credential_username: str | None,
    ) -> GitRepositoryMetrics:
        authorization = request.transport_authorization
        self._validate_authorization(authorization)
        commit_sha = str(request.commit_sha or "").strip().lower()
        if _COMMIT_SHA.fullmatch(commit_sha) is None:
            raise GitConnectorProviderError("git_commit_sha_invalid")
        start = self._monotonic()
        try:
            with self._credential_resolver.open_session(
                credential_ref=authorization.credential_ref,
                credential_username=credential_username,
                scheme=authorization.scheme,
            ) as session:
                with self._workspaces.open() as workspace:
                    self._run_checked(
                        session,
                        hardened_git_network_args(["init", "--quiet", "."]),
                        cwd=workspace,
                        timeout_seconds=15,
                        reason_code="git_workspace_init_failed",
                    )
                    self._run_checked(
                        session,
                        hardened_git_network_args(
                            [
                                "remote",
                                "add",
                                "origin",
                                authorization.canonical_url,
                            ]
                        ),
                        cwd=workspace,
                        timeout_seconds=15,
                        reason_code="git_remote_configuration_failed",
                    )
                    self._run_checked(
                        session,
                        hardened_git_transport_args(
                            authorization,
                            [
                                "fetch",
                                "--no-tags",
                                "--no-recurse-submodules",
                                "--depth=1",
                                "origin",
                                commit_sha,
                            ],
                            remote_name="origin",
                        ),
                        cwd=workspace,
                        timeout_seconds=self._remaining_seconds(
                            start,
                            request.budgets,
                        ),
                        reason_code="git_fetch_failed",
                        maximum_file_bytes=self._fetch_file_limit(
                            request.budgets
                        ),
                    )
                    entries = self._tree_entries(session, workspace)
                    object_count, pack_bytes = self._object_metrics(
                        session,
                        workspace,
                    )
                    metrics = self._metrics_from_entries(
                        entries,
                        object_count=object_count,
                        pack_bytes=pack_bytes,
                        elapsed_seconds=self._monotonic() - start,
                    )
                    self._enforce_budgets(metrics, request.budgets)
                    self._run_checked(
                        session,
                        hardened_git_network_args(
                            [
                                "checkout",
                                "--detach",
                                "--force",
                                "FETCH_HEAD",
                            ]
                        ),
                        cwd=workspace,
                        timeout_seconds=self._remaining_seconds(
                            start,
                            request.budgets,
                        ),
                        reason_code="git_checkout_failed",
                    )
                    lfs_count, lfs_bytes = self._lfs_metrics(
                        workspace,
                        entries,
                    )
                    metrics = replace(
                        metrics,
                        lfs_object_count=lfs_count,
                        lfs_bytes=lfs_bytes,
                        elapsed_seconds=self._monotonic() - start,
                    )
                    self._enforce_budgets(metrics, request.budgets)
                    self._make_checkout_read_only(workspace)
                    return metrics
        except HubGitCredentialError as exc:
            raise GitConnectorProviderError(exc.reason_code) from None

    @staticmethod
    def _validate_authorization(
        authorization: GitTransportAuthorization,
    ) -> None:
        try:
            authorization.validate()
        except GitRemotePolicyError as exc:
            raise GitConnectorProviderError(exc.reason_code) from None

    @staticmethod
    def _ref_candidates(requested_ref: str) -> tuple[str, ...]:
        if _FULL_REF.fullmatch(requested_ref):
            if requested_ref.startswith("refs/tags/"):
                return (f"{requested_ref}^{{}}", requested_ref)
            return (requested_ref,)
        if (
            not requested_ref
            or ".." in requested_ref
            or requested_ref.startswith(("-", "/", "."))
            or any(character.isspace() for character in requested_ref)
        ):
            raise GitConnectorProviderError("git_requested_ref_invalid")
        return (
            f"refs/heads/{requested_ref}",
            f"refs/tags/{requested_ref}^{{}}",
            f"refs/tags/{requested_ref}",
        )

    @staticmethod
    def _parse_ls_remote(stdout: bytes, candidate: str) -> str | None:
        matches: list[str] = []
        for line in stdout.splitlines():
            fields = line.split(b"\t", 1)
            if len(fields) != 2:
                continue
            try:
                commit = fields[0].decode("ascii").lower()
                reference = fields[1].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if reference == candidate and _COMMIT_SHA.fullmatch(commit):
                matches.append(commit)
        if len(set(matches)) > 1:
            raise GitConnectorProviderError("git_ref_ambiguous")
        return matches[0] if matches else None

    def _tree_entries(
        self,
        session: AuthorizedGitCommandSessionPort,
        workspace: Path,
    ) -> tuple[tuple[str, str, int, bytes], ...]:
        result = self._run_checked(
            session,
            hardened_git_network_args(
                ["ls-tree", "-r", "-z", "--long", "FETCH_HEAD"]
            ),
            cwd=workspace,
            timeout_seconds=30,
            reason_code="git_inventory_failed",
        )
        entries: list[tuple[str, str, int, bytes]] = []
        for record in result.stdout.split(b"\x00"):
            if not record:
                continue
            header, separator, raw_path = record.partition(b"\t")
            fields = header.split()
            if (
                not separator
                or len(fields) != 4
                or fields[0]
                not in {b"100644", b"100755", b"120000", b"160000"}
            ):
                raise GitConnectorProviderError(
                    "git_inventory_record_invalid"
                )
            try:
                mode = fields[0].decode("ascii")
                object_id = fields[2].decode("ascii").lower()
                size = 0 if fields[3] == b"-" else int(fields[3])
            except (UnicodeDecodeError, ValueError):
                raise GitConnectorProviderError(
                    "git_inventory_record_invalid"
                ) from None
            if _COMMIT_SHA.fullmatch(object_id) is None:
                raise GitConnectorProviderError(
                    "git_inventory_record_invalid"
                )
            if size < 0 or not raw_path:
                raise GitConnectorProviderError(
                    "git_inventory_record_invalid"
                )
            entries.append((mode, object_id, size, raw_path))
        return tuple(entries)

    def _object_metrics(
        self,
        session: AuthorizedGitCommandSessionPort,
        workspace: Path,
    ) -> tuple[int, int]:
        result = self._run_checked(
            session,
            hardened_git_network_args(["count-objects", "-v"]),
            cwd=workspace,
            timeout_seconds=15,
            reason_code="git_object_inventory_failed",
        )
        values: dict[str, int] = {}
        for line in result.stdout.decode("ascii", errors="ignore").splitlines():
            key, separator, raw_value = line.partition(":")
            if separator:
                try:
                    values[key.strip()] = int(raw_value.strip())
                except ValueError:
                    raise GitConnectorProviderError(
                        "git_object_inventory_invalid"
                    ) from None
        object_count = values.get("count", 0) + values.get("in-pack", 0)
        pack_bytes = (
            values.get("size", 0) + values.get("size-pack", 0)
        ) * 1024
        return object_count, pack_bytes

    @staticmethod
    def _metrics_from_entries(
        entries: tuple[tuple[str, str, int, bytes], ...],
        *,
        object_count: int,
        pack_bytes: int,
        elapsed_seconds: float,
    ) -> GitRepositoryMetrics:
        manifest = hashlib.sha256()
        file_sizes: list[int] = []
        submodule_count = 0
        exclusion_counts: dict[str, int] = {}
        for mode, object_id, size, path in sorted(
            entries,
            key=lambda item: item[3],
        ):
            manifest.update(mode.encode("ascii"))
            manifest.update(b"\x00")
            manifest.update(object_id.encode("ascii"))
            manifest.update(b"\x00")
            manifest.update(str(size).encode("ascii"))
            manifest.update(b"\x00")
            manifest.update(path)
            manifest.update(b"\x00")
            if mode == "160000":
                submodule_count += 1
                exclusion_counts["git_submodule_excluded"] = (
                    exclusion_counts.get("git_submodule_excluded", 0) + 1
                )
            elif mode == "120000":
                exclusion_counts["git_symlink_excluded"] = (
                    exclusion_counts.get("git_symlink_excluded", 0) + 1
                )
                file_sizes.append(size)
            else:
                file_sizes.append(size)
        return GitRepositoryMetrics(
            item_count=len(entries),
            object_count=object_count,
            pack_bytes=pack_bytes,
            file_count=len(file_sizes),
            largest_file_bytes=max(file_sizes, default=0),
            total_file_bytes=sum(file_sizes),
            submodule_count=submodule_count,
            lfs_object_count=0,
            lfs_bytes=0,
            elapsed_seconds=max(0.0, elapsed_seconds),
            egress_bytes=max(0, pack_bytes),
            manifest_digest=manifest.hexdigest(),
            exclusions=tuple(
                {
                    "reason_code": reason_code,
                    "count": exclusion_counts[reason_code],
                }
                for reason_code in sorted(exclusion_counts)
            ),
        )

    @staticmethod
    def _lfs_metrics(
        workspace: Path,
        entries: tuple[tuple[str, str, int, bytes], ...],
    ) -> tuple[int, int]:
        count = 0
        total = 0
        root = workspace.resolve()
        for mode, _object_id, size, raw_path in entries:
            if mode not in {"100644", "100755"} or size > 1024:
                continue
            relative = Path(os.fsdecode(raw_path))
            candidate = workspace / relative
            try:
                resolved_parent = candidate.parent.resolve(strict=True)
            except OSError:
                continue
            if not resolved_parent.is_relative_to(root):
                raise GitConnectorProviderError(
                    "git_checkout_path_escape"
                )
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                descriptor = os.open(candidate, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    payload = handle.read(1025)
            except OSError:
                continue
            if payload.startswith(
                b"version https://git-lfs.github.com/spec/v1\n"
            ):
                count += 1
                for line in payload.splitlines():
                    if line.startswith(b"size "):
                        try:
                            total += int(line[5:])
                        except ValueError:
                            raise GitConnectorProviderError(
                                "git_lfs_pointer_invalid"
                            ) from None
        return count, total

    @staticmethod
    def _make_checkout_read_only(workspace: Path) -> None:
        git_root = workspace / ".git"
        for current, directories, files in os.walk(
            workspace,
            topdown=False,
            followlinks=False,
        ):
            current_path = Path(current)
            if current_path == git_root or git_root in current_path.parents:
                continue
            for name in files:
                path = current_path / name
                if not path.is_symlink():
                    path.chmod(stat.S_IRUSR)
            for name in directories:
                path = current_path / name
                if path != git_root and not path.is_symlink():
                    path.chmod(stat.S_IRUSR | stat.S_IXUSR)

    def _remaining_seconds(
        self,
        start: float,
        budgets: GitRepositoryBudgets,
    ) -> float:
        limit = float(getattr(budgets, "max_elapsed_seconds", 120.0))
        remaining = limit - (self._monotonic() - start)
        if remaining <= 0:
            raise GitConnectorProviderError("git_time_budget_exceeded")
        return remaining

    @staticmethod
    def _fetch_file_limit(budgets: GitRepositoryBudgets) -> int:
        pack_limit = getattr(budgets, "max_pack_bytes", None)
        egress_limit = getattr(budgets, "max_egress_bytes", None)
        if pack_limit is None or egress_limit is None:
            raise GitConnectorProviderError("git_fetch_budget_missing")
        if int(pack_limit) <= 0:
            raise GitConnectorProviderError("git_pack_budget_exceeded")
        if int(egress_limit) <= 0:
            raise GitConnectorProviderError("git_egress_budget_exceeded")
        return min(int(pack_limit), int(egress_limit))

    @staticmethod
    def _enforce_budgets(
        metrics: GitRepositoryMetrics,
        budgets: GitRepositoryBudgets,
    ) -> None:
        checks = (
            ("object_count", "max_objects", "git_object_budget_exceeded"),
            ("pack_bytes", "max_pack_bytes", "git_pack_budget_exceeded"),
            ("file_count", "max_files", "git_file_count_budget_exceeded"),
            (
                "largest_file_bytes",
                "max_file_bytes",
                "git_file_budget_exceeded",
            ),
            (
                "total_file_bytes",
                "max_total_file_bytes",
                "git_total_file_budget_exceeded",
            ),
            (
                "submodule_count",
                "max_submodules",
                "git_submodule_budget_exceeded",
            ),
            (
                "lfs_object_count",
                "max_lfs_objects",
                "git_lfs_budget_exceeded",
            ),
            ("lfs_bytes", "max_lfs_bytes", "git_lfs_budget_exceeded"),
            (
                "elapsed_seconds",
                "max_elapsed_seconds",
                "git_time_budget_exceeded",
            ),
            (
                "egress_bytes",
                "max_egress_bytes",
                "git_egress_budget_exceeded",
            ),
        )
        for metric_name, budget_name, reason_code in checks:
            limit = getattr(budgets, budget_name, None)
            if limit is not None and getattr(metrics, metric_name) > limit:
                raise GitConnectorProviderError(reason_code)

    @staticmethod
    def _run_checked(
        session: AuthorizedGitCommandSessionPort,
        arguments: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        reason_code: str,
        maximum_file_bytes: int | None = None,
    ) -> GitCommandResult:
        result = session.run(
            arguments,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            maximum_file_bytes=maximum_file_bytes,
        )
        HubGitTransport._require_result(result, reason_code)
        return result

    @staticmethod
    def _require_result(
        result: GitCommandResult,
        reason_code: str,
    ) -> None:
        if result.timed_out:
            raise GitConnectorProviderError("git_time_budget_exceeded")
        if result.output_truncated:
            raise GitConnectorProviderError(
                "git_command_output_budget_exceeded"
            )
        if result.returncode != 0:
            raise GitConnectorProviderError(reason_code)


__all__ = [
    "HubGitTransport",
    "HubGitTransportPort",
    "SecureTemporaryGitWorkspace",
]

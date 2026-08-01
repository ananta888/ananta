"""Fail-closed composite for independently persisted registered remotes."""

from __future__ import annotations

from collections.abc import Sequence

from agent.sources.git_source_connector_common import GitSourceScope


class CompositeRegisteredRemoteCatalog:
    def __init__(self, registries: Sequence[object]) -> None:
        self._registries = tuple(registries)
        if not self._registries:
            raise ValueError("registered_remote_registry_required")

    def contains(self, registry: object) -> bool:
        return any(item is registry for item in self._registries)

    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[object, ...]:
        by_origin: dict[str, set[int]] = {}
        values: list[tuple[int, object]] = []
        for index, registry in enumerate(self._registries):
            method = getattr(registry, "list_authorizations", None)
            if not callable(method):
                raise RuntimeError("registered_remote_registry_invalid")
            for record in method(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            ):
                reference = str(
                    getattr(record, "connection_ref", "") or ""
                )
                by_origin.setdefault(reference, set()).add(index)
                values.append((index, record))
        return tuple(
            record
            for index, record in sorted(
                values,
                key=lambda item: (
                    str(getattr(item[1], "connection_ref", "")),
                    str(getattr(item[1], "repository", "") or ""),
                ),
            )
            if len(
                by_origin.get(
                    str(getattr(record, "connection_ref", "") or ""),
                    set(),
                )
            )
            == 1
        )

    def resolve_registered_remote(self, **kwargs):
        return self._resolve("resolve_registered_remote", **kwargs)

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ):
        return self._resolve(
            "resolve_connection",
            scope=scope,
            connection_ref=connection_ref,
            repository_identifier=repository_identifier,
        )

    def resolve_github(self, **kwargs):
        return self._resolve("resolve_github", **kwargs)

    def resolve_generic(self, **kwargs):
        return self._resolve("resolve_generic", **kwargs)

    def _resolve(self, method_name: str, **kwargs):
        matches = []
        for registry in self._registries:
            method = getattr(registry, method_name, None)
            if not callable(method):
                raise RuntimeError("registered_remote_registry_invalid")
            value = method(**kwargs)
            if value is not None:
                matches.append(value)
        return matches[0] if len(matches) == 1 else None


__all__ = ["CompositeRegisteredRemoteCatalog"]

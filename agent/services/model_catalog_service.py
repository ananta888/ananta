"""Hub-owned model catalog and safe default-selection domain services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCatalog,
    ModelDefaultSelection,
    ModelDefaultSelectionCommand,
    ModelHealth,
    ModelRuntime,
    ModelSummary,
    ProviderCatalogFailure,
)

MODEL_CATALOG_REFRESH_CAPABILITY = "model_catalog.refresh"
MODEL_DEFAULT_SELECT_CAPABILITY = "model_catalog.set_default"


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    default_provider: str
    default_model: str
    task_kind: str = ""
    timeout_seconds: int = 3
    cache_ttl_seconds: int = 30
    force_refresh: bool = False


@dataclass(frozen=True, slots=True)
class ProviderDiscovery:
    models: tuple[Mapping[str, Any], ...]
    available: bool
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    legacy_catalog: Mapping[str, Any]
    provider_failures: tuple[ProviderCatalogFailure, ...]


class ProviderInventoryPort(Protocol):
    def list_specs(self, query: CatalogQuery) -> Sequence[Mapping[str, Any]]: ...

    def discover(
        self,
        provider: Mapping[str, Any],
        query: CatalogQuery,
    ) -> ProviderDiscovery: ...

    def voice_entry(self) -> Mapping[str, Any]: ...


class CatalogPolicyPort(Protocol):
    def benchmark_rows(
        self,
        task_kind: str,
    ) -> tuple[Sequence[Mapping[str, Any]], Mapping[str, Any]]: ...

    def routing_decision(
        self,
        provider_entry: Mapping[str, Any],
        task_kind: str,
    ) -> Mapping[str, Any]: ...

    def fallback_policy(self) -> Mapping[str, Any]: ...


class ModelCatalogPort(Protocol):
    def snapshot(self, query: CatalogQuery) -> CatalogSnapshot: ...

    def versioned_catalog(self, query: CatalogQuery) -> ModelCatalog: ...


class DefaultSelectionStorePort(Protocol):
    def save(self, *, provider_id: str, model_id: str) -> None: ...


class DefaultSelectionRuntimePort(Protocol):
    def apply(self, *, provider_id: str, model_id: str) -> None: ...


class ModelDefaultSelectionError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class ModelCatalogCapabilityPolicy:
    """Explicit capability gate with admin compatibility and auth-disabled deny."""

    def allows(
        self,
        capability: str,
        *,
        is_admin: bool,
        claims: Mapping[str, Any] | None,
    ) -> bool:
        payload = claims if isinstance(claims, Mapping) else {}
        if payload.get("auth_mode") == "auth_disabled":
            return False
        if is_admin:
            return True
        raw = payload.get("capabilities")
        capabilities = (
            {str(value).strip() for value in raw}
            if isinstance(raw, (list, tuple, set))
            else set()
        )
        return capability in capabilities


def _provider_id(value: object, *, fallback: str = "catalog") -> str:
    normalized = str(value or "").strip().lower()
    if normalized and normalized[0].isalnum() and all(
        character.isalnum() or character in "_.:/@+-"
        for character in normalized
    ):
        return normalized[:256]
    return fallback


def _decorate_model(
    provider_id: str,
    model_id: str,
    item: Mapping[str, Any],
    task_kind: str,
    benchmark_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    enriched = dict(item)
    if not task_kind:
        return enriched
    bench = benchmark_index.get(f"{provider_id}:{model_id}")
    if bench:
        row = bench.get("row")
        enriched["benchmark"] = (
            (row.get("focus") or {}) if isinstance(row, Mapping) else {}
        )
        enriched["recommended_rank"] = bench.get("rank")
    return enriched


def _catalog_entry(
    provider_id: str,
    base_url: str | None,
    available: bool,
    models: list[dict[str, Any]],
    *,
    capabilities: Mapping[str, Any] | None = None,
    task_kind: str = "",
) -> dict[str, Any]:
    recommended_model = None
    if task_kind:
        ranked = [
            item
            for item in models
            if isinstance(item, Mapping) and item.get("recommended_rank")
        ]
        ranked.sort(key=lambda item: int(item.get("recommended_rank") or 9999))
        recommended_model = ranked[0].get("id") if ranked else None
    return {
        "provider": provider_id,
        "base_url": base_url,
        "available": bool(available),
        "model_count": len(models),
        "models": models,
        "capabilities": dict(capabilities or {}),
        "recommended_model": recommended_model,
    }


class ModelCatalogService:
    def __init__(
        self,
        *,
        inventory: ProviderInventoryPort,
        policy: CatalogPolicyPort,
    ) -> None:
        self._inventory = inventory
        self._policy = policy

    def snapshot(self, query: CatalogQuery) -> CatalogSnapshot:
        failures: list[ProviderCatalogFailure] = []

        def record(provider_id: object, reason_code: str) -> None:
            candidate = ProviderCatalogFailure(
                provider_id=_provider_id(provider_id),
                reason_code=reason_code,
            )
            if candidate not in failures:
                failures.append(candidate)

        try:
            benchmark_rows, benchmark_db = self._policy.benchmark_rows(
                query.task_kind
            )
        except Exception:
            benchmark_rows, benchmark_db = (), {}
            record("benchmark", "benchmark_catalog_unavailable")
        benchmark_index = {
            str(item.get("id") or ""): {"rank": index + 1, "row": item}
            for index, item in enumerate(benchmark_rows)
            if isinstance(item, Mapping)
        }
        legacy: dict[str, Any] = {
            "default_provider": query.default_provider,
            "default_model": query.default_model,
            "providers": [],
        }
        available_model_ids: set[str] = set()
        try:
            specs = [
                dict(item)
                for item in self._inventory.list_specs(query)
                if isinstance(item, Mapping)
            ]
        except Exception:
            specs = []
            record("catalog", "provider_registry_unavailable")

        dynamic = [
            item
            for item in specs
            if bool((item.get("capabilities") or {}).get("dynamic_models"))
        ]
        for backend in dynamic:
            provider_id = _provider_id(backend.get("provider"))
            try:
                discovery = self._inventory.discover(backend, query)
            except Exception:
                discovery = ProviderDiscovery(
                    models=(),
                    available=False,
                    metadata={
                        "status": "unavailable",
                        "source": "configured_fallback",
                        "used_configured_fallback": True,
                    },
                )
                record(provider_id, "provider_model_discovery_failed")
            discovered = list(discovery.models)
            if not discovered:
                discovered = [
                    {"id": model_id}
                    for model_id in list(backend.get("models") or [])
                ]
            models: list[dict[str, Any]] = []
            for item in discovered:
                if not isinstance(item, Mapping):
                    record(provider_id, "provider_model_descriptor_invalid")
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    record(provider_id, "provider_model_descriptor_invalid")
                    continue
                if discovery.available:
                    available_model_ids.add(f"{provider_id}:{model_id}")
                models.append(
                    _decorate_model(
                        provider_id,
                        model_id,
                        {
                            "id": model_id,
                            "display_name": item.get("display_name") or model_id,
                            "context_length": item.get("context_length"),
                            "quantization": item.get("quantization"),
                            "loaded": item.get("loaded"),
                            "available": bool(
                                item.get("available", discovery.available)
                            ),
                            "source": item.get("source"),
                            "selected": (
                                query.default_provider == provider_id
                                and query.default_model == model_id
                            ),
                            "capabilities": item.get("capabilities"),
                        },
                        query.task_kind,
                        benchmark_index,
                    )
                )
            entry = _catalog_entry(
                provider_id,
                backend.get("base_url"),
                discovery.available
                and bool(models or list(backend.get("models") or [])),
                models,
                capabilities={
                    **dict(backend.get("capabilities") or {}),
                    "dynamic_models": True,
                    "supports_chat": True,
                    "openai_compatible": True,
                    "transport_provider": backend.get("transport_provider"),
                    "supports_tool_calls": bool(
                        backend.get("supports_tool_calls")
                    ),
                    "provider_type": backend.get("provider_type")
                    or "local_openai_compatible",
                    "remote_hub": bool(backend.get("remote_hub")),
                    "instance_id": backend.get("instance_id"),
                    "max_hops": backend.get("max_hops"),
                    "remote_hub_policy": (
                        backend.get("capabilities") or {}
                    ).get("remote_hub_policy"),
                    "federation_policy": (
                        backend.get("capabilities") or {}
                    ).get("federation_policy"),
                    "trust_level": backend.get("trust_level"),
                    "allowed_operations": list(
                        backend.get("allowed_operations") or []
                    ),
                    "allow_artifact_access": bool(
                        backend.get("allow_artifact_access", False)
                    ),
                    "allow_file_access": bool(
                        backend.get("allow_file_access", False)
                    ),
                },
                task_kind=query.task_kind,
            )
            if discovery.metadata:
                entry["model_discovery"] = dict(discovery.metadata)
            entry["routing_decision"] = self._routing_decision(
                entry={**entry, **backend},
                task_kind=query.task_kind,
                provider_id=provider_id,
                record=record,
            )
            legacy["providers"].append(entry)

        static = [
            item
            for item in specs
            if not bool((item.get("capabilities") or {}).get("dynamic_models"))
        ]
        for provider in static:
            provider_id = _provider_id(provider.get("provider"))
            models: list[dict[str, Any]] = []
            for raw_model_id in list(provider.get("models") or []):
                model_id = str(raw_model_id or "").strip()
                if not model_id:
                    record(provider_id, "provider_model_descriptor_invalid")
                    continue
                if bool(provider.get("available")):
                    available_model_ids.add(f"{provider_id}:{model_id}")
                models.append(
                    _decorate_model(
                        provider_id,
                        model_id,
                        {
                            "id": model_id,
                            "display_name": model_id,
                            "selected": (
                                query.default_provider == provider_id
                                and query.default_model == model_id
                            ),
                        },
                        query.task_kind,
                        benchmark_index,
                    )
                )
            entry = _catalog_entry(
                provider_id,
                provider.get("base_url"),
                bool(provider.get("available")),
                models,
                capabilities=provider.get("capabilities"),
                task_kind=query.task_kind,
            )
            entry["routing_decision"] = self._routing_decision(
                entry={**entry, **provider},
                task_kind=query.task_kind,
                provider_id=provider_id,
                record=record,
            )
            legacy["providers"].append(entry)

        try:
            voice = dict(self._inventory.voice_entry())
            voice_id = _provider_id(voice.get("provider"), fallback="voice")
            voice["routing_decision"] = {
                "provider": voice_id,
                "provider_type": "local_voice_runtime",
                "eligible_for_inference": False,
                "eligible_for_execution": True,
                "availability": (
                    "available" if voice.get("available") else "degraded"
                ),
                "reason": "voice_runtime_health_probe",
            }
            legacy["providers"].append(voice)
            reason = (voice.get("capabilities") or {}).get("status_reason")
            if reason:
                record(voice_id, "voice_provider_unavailable")
        except Exception:
            record("voice", "voice_provider_unavailable")

        if query.task_kind:
            recommendations = [
                {
                    "id": row.get("id"),
                    "provider": row.get("provider"),
                    "model": row.get("model"),
                    "suitability_score": (
                        (row.get("focus") or {}).get("suitability_score")
                    ),
                    "available": str(row.get("id") or "")
                    in available_model_ids,
                }
                for row in benchmark_rows[:5]
                if isinstance(row, Mapping)
            ]
            legacy["recommendations"] = {
                "task_kind": query.task_kind,
                "updated_at": benchmark_db.get("updated_at"),
                "items": recommendations,
            }
            selected = next(
                (item for item in recommendations if item.get("available")),
                None,
            )
            if selected:
                legacy["selection"] = {
                    "task_kind": query.task_kind,
                    "provider": selected.get("provider"),
                    "model": selected.get("model"),
                    "id": selected.get("id"),
                    "selection_source": "benchmarks_available_top_ranked",
                }
        try:
            legacy["routing_fallback_policy"] = dict(
                self._policy.fallback_policy()
            )
        except Exception:
            legacy["routing_fallback_policy"] = {}
            record("catalog", "routing_fallback_policy_unavailable")
        return CatalogSnapshot(
            legacy_catalog=legacy,
            provider_failures=tuple(failures),
        )

    def _routing_decision(
        self,
        *,
        entry: Mapping[str, Any],
        task_kind: str,
        provider_id: str,
        record,
    ) -> Mapping[str, Any]:
        try:
            return dict(self._policy.routing_decision(entry, task_kind))
        except Exception:
            record(provider_id, "provider_routing_decision_failed")
            return {
                "provider": provider_id,
                "eligible_for_inference": False,
                "eligible_for_execution": False,
                "availability": "unavailable",
                "reason": "routing_decision_unavailable",
            }

    def versioned_catalog(self, query: CatalogQuery) -> ModelCatalog:
        snapshot = self.snapshot(query)
        failures = list(snapshot.provider_failures)
        models: dict[tuple[str, str], ModelSummary] = {}
        for raw_provider in snapshot.legacy_catalog.get("providers", []):
            if not isinstance(raw_provider, Mapping):
                continue
            provider_id = _provider_id(raw_provider.get("provider"))
            capabilities = (
                raw_provider.get("capabilities")
                if isinstance(raw_provider.get("capabilities"), Mapping)
                else {}
            )
            runtime = self._runtime(provider_id, capabilities)
            available = bool(raw_provider.get("available"))
            status = str(capabilities.get("status") or "").lower()
            availability = (
                ModelAvailability.AVAILABLE
                if available
                else (
                    ModelAvailability.DEGRADED
                    if status == "degraded"
                    else ModelAvailability.UNAVAILABLE
                )
            )
            health = (
                ModelHealth.HEALTHY
                if available and status not in {"degraded", "unavailable"}
                else (
                    ModelHealth.DEGRADED
                    if status == "degraded"
                    else ModelHealth.UNAVAILABLE
                )
            )
            provider_capabilities = self._capabilities(capabilities)
            for raw_model in raw_provider.get("models") or []:
                if not isinstance(raw_model, Mapping):
                    continue
                model_id = str(raw_model.get("id") or "").strip()
                try:
                    summary = ModelSummary(
                        provider_id=provider_id,
                        runtime=runtime,
                        model_id=model_id,
                        display_name=str(
                            raw_model.get("display_name") or model_id
                        ),
                        availability=(
                            availability
                            if raw_model.get("available", available)
                            else ModelAvailability.UNAVAILABLE
                        ),
                        loaded=(
                            raw_model.get("loaded")
                            if type(raw_model.get("loaded")) is bool
                            else None
                        ),
                        context_window=self._positive_int(
                            raw_model.get("context_window")
                            or raw_model.get("context_length")
                        ),
                        quantization=(
                            str(raw_model.get("quantization")).strip() or None
                            if raw_model.get("quantization") is not None
                            else None
                        ),
                        capabilities=tuple(
                            sorted(
                                provider_capabilities
                                | self._capabilities(raw_model)
                            )
                        ),
                        health=health,
                        is_default=bool(raw_model.get("selected")),
                    )
                except ValueError:
                    failure = ProviderCatalogFailure(
                        provider_id=provider_id,
                        reason_code="provider_model_contract_invalid",
                    )
                    if failure not in failures:
                        failures.append(failure)
                    continue
                models[(summary.provider_id, summary.model_id)] = summary
        default_selection = None
        default_provider = str(
            snapshot.legacy_catalog.get("default_provider") or ""
        ).strip()
        default_model = str(
            snapshot.legacy_catalog.get("default_model") or ""
        ).strip()
        if default_provider and default_model:
            try:
                default_selection = ModelDefaultSelection(
                    provider_id=default_provider,
                    model_id=default_model,
                )
            except ValueError:
                failure = ProviderCatalogFailure(
                    provider_id="catalog",
                    reason_code="default_selection_contract_invalid",
                )
                if failure not in failures:
                    failures.append(failure)
        return ModelCatalog(
            default_selection=default_selection,
            models=tuple(models[key] for key in sorted(models)),
            provider_failures=tuple(
                sorted(
                    failures,
                    key=lambda item: (item.provider_id, item.reason_code),
                )
            ),
        )

    @staticmethod
    def _runtime(
        provider_id: str,
        capabilities: Mapping[str, Any],
    ) -> ModelRuntime:
        provider_type = str(capabilities.get("provider_type") or "").lower()
        if provider_type == "local_voice_runtime":
            return ModelRuntime.VOICE
        if capabilities.get("remote_hub") or "remote" in provider_type:
            return ModelRuntime.REMOTE
        if "local" in provider_type or capabilities.get("dynamic_models"):
            return ModelRuntime.LOCAL
        if provider_id in {"openai", "anthropic", "openrouter", "codex"}:
            return ModelRuntime.CLOUD
        return ModelRuntime.UNKNOWN

    @staticmethod
    def _capabilities(values: Mapping[str, Any]) -> set[str]:
        result = {
            str(key).removeprefix("supports_").strip().lower()
            for key, value in values.items()
            if str(key).startswith("supports_") and value is True
        }
        listed = values.get("capabilities") or values.get("voice_capabilities")
        if isinstance(listed, (list, tuple, set)):
            result.update(
                str(value).strip().lower()
                for value in listed
                if str(value).strip()
            )
        return result

    @staticmethod
    def _positive_int(value: object) -> int | None:
        if type(value) is int and 1 <= value <= 100_000_000:
            return value
        return None


class ModelDefaultSelectionService:
    def __init__(
        self,
        *,
        catalog: ModelCatalogPort,
        store: DefaultSelectionStorePort,
        runtime: DefaultSelectionRuntimePort,
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._runtime = runtime

    def select(
        self,
        command: ModelDefaultSelectionCommand,
        *,
        query: CatalogQuery,
    ) -> ModelDefaultSelection:
        catalog = self._catalog.versioned_catalog(query)
        selected = next(
            (
                item
                for item in catalog.models
                if item.provider_id == command.provider_id
                and item.model_id == command.model_id
            ),
            None,
        )
        if selected is None:
            raise ModelDefaultSelectionError(
                "model_default_selection_not_allowlisted",
                status_code=404,
            )
        if (
            selected.availability is not ModelAvailability.AVAILABLE
            or selected.health is not ModelHealth.HEALTHY
        ):
            raise ModelDefaultSelectionError(
                "model_default_selection_unavailable",
                status_code=409,
            )
        self._store.save(
            provider_id=selected.provider_id,
            model_id=selected.model_id,
        )
        self._runtime.apply(
            provider_id=selected.provider_id,
            model_id=selected.model_id,
        )
        return ModelDefaultSelection(
            provider_id=selected.provider_id,
            model_id=selected.model_id,
        )


__all__ = [
    "MODEL_CATALOG_REFRESH_CAPABILITY",
    "MODEL_DEFAULT_SELECT_CAPABILITY",
    "CatalogPolicyPort",
    "CatalogQuery",
    "CatalogSnapshot",
    "DefaultSelectionRuntimePort",
    "DefaultSelectionStorePort",
    "ModelCatalogCapabilityPolicy",
    "ModelCatalogPort",
    "ModelCatalogService",
    "ModelDefaultSelectionError",
    "ModelDefaultSelectionService",
    "ProviderDiscovery",
    "ProviderInventoryPort",
]

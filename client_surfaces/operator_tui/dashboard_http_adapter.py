from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from client_surfaces.operator_tui.dashboard_auth import (
    DashboardReauthenticationRequired,
    DashboardTokenProvider,
    ResolvingDashboardTokenProvider,
)
from client_surfaces.operator_tui.dashboard_surfaces import (
    DashboardFeatureFlags,
    DashboardSurfaceController,
    RevisionConflict,
)
from client_surfaces.operator_tui.ops_api_client import OpsApiClient, OpsApiHttpError

_FEATURES_PATH = "/config/features/v1"
_KANBAN_ROOT = "/api/v1/kanban"
_MODEL_CATALOG_PATH = "/models/catalog/v1"
_MODEL_REFRESH_PATH = "/models/catalog/v1/refresh"
_MODEL_CATALOG_V2_PATH = "/models/catalog/v2"
_MODEL_REFRESH_V2_PATH = "/models/catalog/v2/refresh"
_MODEL_DEFAULT_PATH = "/models/default/v1"
_FEATURE_SCHEMA = "ananta.dashboard-feature-flags.v1"
_MODEL_CATALOG_SCHEMA = "ananta.model-catalog.v1"
_MODEL_CATALOG_V2_SCHEMA = "ananta.model-catalog.v2"
_MODEL_DEFAULT_COMMAND_SCHEMA = "ananta.model-default-selection-command.v1"
_KANBAN_SCHEMA = "kanban.v1"
_KANBAN_SNAPSHOT_SCHEMA = "kanban.snapshot.v1"


class DashboardHttpError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int,
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code or "dashboard_http_error")
        self.status_code = int(status_code)
        self.details = dict(details or {})
        super().__init__(str(message or self.code))


class DashboardPermissionError(PermissionError):
    def __init__(self, code: str, *, status_code: int, message: str = "") -> None:
        self.code = str(code)
        self.status_code = int(status_code)
        super().__init__(str(message or code))


class DashboardHubAdapter:
    """Hub-relative HTTP adapter for the existing Kanban and model catalog ports."""

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        local_flags: DashboardFeatureFlags | None = None,
        board_id: str = "hub",
        timeout_seconds: float = 5.0,
        idempotency_key_factory: Callable[[], str] | None = None,
        token_provider: DashboardTokenProvider | None = None,
    ) -> None:
        base = str(endpoint or "").strip().rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("dashboard_hub_endpoint_invalid")
        self._endpoint = base
        self._token_provider = token_provider or ResolvingDashboardTokenProvider(
            endpoint=base,
            credential=token,
        )
        self._local_flags = local_flags or DashboardFeatureFlags.from_mapping()
        self._board_id = str(board_id or "").strip()
        if not self._board_id:
            raise ValueError("dashboard_board_id_required")
        self._timeout_seconds = max(0.25, min(30.0, float(timeout_seconds)))
        self._idempotency_key_factory = idempotency_key_factory or (
            lambda: f"tui-{uuid.uuid4().hex}"
        )
        self._model_revision = ""
        self._model_providers: dict[str, tuple[str, ...]] = {}

    def _http_client(self, *, force_refresh: bool = False) -> OpsApiClient:
        token = self._token_provider.access_token(force_refresh=force_refresh)
        return OpsApiClient(self._endpoint, token)

    def _request_sync(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None,
        force_refresh: bool,
    ) -> dict[str, Any]:
        return self._http_client(force_refresh=force_refresh).request_json(
            method,
            path,
            payload=payload,
            timeout=self._timeout_seconds,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        force_refresh = False
        for attempt in range(2):
            try:
                return await asyncio.to_thread(
                    self._request_sync,
                    method,
                    path,
                    payload=payload,
                    force_refresh=force_refresh,
                )
            except DashboardReauthenticationRequired as exc:
                raise DashboardPermissionError(
                    exc.code,
                    status_code=401,
                    message=str(exc),
                ) from exc
            except OpsApiHttpError as exc:
                if exc.status_code == 401 and attempt == 0:
                    force_refresh = True
                    continue
                if exc.status_code == 409:
                    details = exc.payload.get("error")
                    details = details.get("details") if isinstance(details, dict) else {}
                    current_revision = (
                        details.get("current_revision")
                        if isinstance(details, dict)
                        else None
                    )
                    raise RevisionConflict(
                        exc.code,
                        current_revision=current_revision,
                    ) from exc
                if exc.status_code in {401, 403}:
                    raise DashboardPermissionError(
                        exc.code,
                        status_code=exc.status_code,
                        message=str(exc),
                    ) from exc
                nested = exc.payload.get("error")
                details = nested.get("details") if isinstance(nested, dict) else {}
                raise DashboardHttpError(
                    exc.code,
                    status_code=exc.status_code,
                    message=str(exc),
                    details=details if isinstance(details, dict) else {},
                ) from exc
        raise DashboardHttpError(
            "dashboard_request_retry_exhausted",
            status_code=503,
        )

    @staticmethod
    def _data(response: Mapping[str, Any]) -> Any:
        if "data" not in response:
            raise DashboardHttpError(
                "dashboard_response_data_missing",
                status_code=502,
            )
        return response["data"]

    async def _require_feature(self, feature: str) -> None:
        local_enabled = (
            self._local_flags.kanban if feature == "tui_kanban" else self._local_flags.models
        )
        if not local_enabled:
            raise DashboardHttpError(
                f"{feature}_disabled_local",
                status_code=404,
            )
        data = self._data(await self._request("GET", _FEATURES_PATH))
        if not isinstance(data, Mapping) or data.get("schema") != _FEATURE_SCHEMA:
            raise DashboardHttpError(
                "dashboard_feature_contract_invalid",
                status_code=502,
            )
        features = data.get("features")
        if not isinstance(features, Mapping) or type(features.get(feature)) is not bool:
            raise DashboardHttpError(
                "dashboard_feature_contract_invalid",
                status_code=502,
            )
        if features[feature] is not True:
            raise DashboardHttpError(
                f"{feature}_disabled_backend",
                status_code=404,
            )

    @staticmethod
    def _require_mapping(data: Any, *, code: str) -> Mapping[str, Any]:
        if not isinstance(data, Mapping):
            raise DashboardHttpError(code, status_code=502)
        return data

    async def fetch_board(self) -> Mapping[str, Any]:
        await self._require_feature("tui_kanban")
        try:
            return await self._fetch_atomic_board_snapshot()
        except DashboardHttpError as exc:
            if exc.status_code != 404:
                raise
        return await self._fetch_legacy_board()

    async def _fetch_atomic_board_snapshot(self) -> Mapping[str, Any]:
        encoded_board = urllib.parse.quote(self._board_id, safe="")
        snapshot = self._require_mapping(
            self._data(
                await self._request(
                    "GET",
                    f"{_KANBAN_ROOT}/boards/{encoded_board}/snapshot",
                )
            ),
            code="kanban_snapshot_contract_invalid",
        )
        if snapshot.get("schema_version") != _KANBAN_SNAPSHOT_SCHEMA:
            raise DashboardHttpError(
                "kanban_snapshot_contract_invalid",
                status_code=502,
            )
        board = self._require_mapping(
            snapshot.get("board"),
            code="kanban_snapshot_contract_invalid",
        )
        raw_cards = snapshot.get("cards")
        raw_columns = board.get("columns")
        if not isinstance(raw_cards, list) or not isinstance(raw_columns, list):
            raise DashboardHttpError(
                "kanban_snapshot_contract_invalid",
                status_code=502,
            )
        event_sequence = self._revision(snapshot.get("event_sequence"))
        board_id = str(board.get("id") or "")
        if board_id != self._board_id:
            raise DashboardHttpError(
                "kanban_snapshot_board_mismatch",
                status_code=502,
            )
        columns: list[dict[str, Any]] = [
            {
                "id": str(column.get("id") or ""),
                "title": str(column.get("title") or column.get("id") or ""),
                "wip_limit": None,
                "tasks": [],
            }
            for column in raw_columns
            if isinstance(column, Mapping)
        ]
        by_id = {column["id"]: column for column in columns}
        for card in raw_cards:
            if (
                not isinstance(card, Mapping)
                or str(card.get("board_id") or "") != board_id
            ):
                raise DashboardHttpError(
                    "kanban_snapshot_card_contract_invalid",
                    status_code=502,
                )
            column = by_id.get(str(card.get("column_id") or ""))
            if column is None:
                raise DashboardHttpError(
                    "kanban_card_column_unknown",
                    status_code=502,
                )
            assignee = card.get("assignee")
            assignee_id = (
                str(assignee.get("id") or "")
                if isinstance(assignee, Mapping)
                else ""
            )
            column["tasks"].append(
                {
                    "id": str(card.get("id") or ""),
                    "title": str(card.get("title") or card.get("id") or ""),
                    "description": str(card.get("description") or ""),
                    "status": str(card.get("status") or ""),
                    "priority": str(card.get("priority") or ""),
                    "assignee_id": assignee_id,
                    "labels": list(card.get("labels") or []),
                    "blocked": bool(card.get("blocked")),
                    "dependencies": list(card.get("dependencies") or []),
                    "revision": card.get("revision"),
                }
            )
        return {
            "board_id": board_id,
            "revision": board.get("revision"),
            "event_sequence": event_sequence,
            "columns": columns,
        }

    async def _fetch_legacy_board(self) -> Mapping[str, Any]:
        encoded_board = urllib.parse.quote(self._board_id, safe="")
        board = self._require_mapping(
            self._data(
                await self._request(
                    "GET",
                    f"{_KANBAN_ROOT}/boards/{encoded_board}",
                )
            ),
            code="kanban_board_contract_invalid",
        )
        if board.get("schema_version") != _KANBAN_SCHEMA:
            raise DashboardHttpError("kanban_board_contract_invalid", status_code=502)
        raw_columns = board.get("columns")
        if not isinstance(raw_columns, list):
            raise DashboardHttpError("kanban_board_contract_invalid", status_code=502)
        columns: list[dict[str, Any]] = [
            {
                "id": str(column.get("id") or ""),
                "title": str(column.get("title") or column.get("id") or ""),
                "wip_limit": None,
                "tasks": [],
            }
            for column in raw_columns
            if isinstance(column, Mapping)
        ]
        by_id = {column["id"]: column for column in columns}

        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(100):
            query = {"limit": "200"}
            if cursor:
                query["cursor"] = cursor
            page = self._require_mapping(
                self._data(
                    await self._request(
                        "GET",
                        f"{_KANBAN_ROOT}/boards/{encoded_board}/cards?"
                        + urllib.parse.urlencode(query),
                    )
                ),
                code="kanban_card_page_contract_invalid",
            )
            if page.get("schema_version") != _KANBAN_SCHEMA:
                raise DashboardHttpError(
                    "kanban_card_page_contract_invalid",
                    status_code=502,
                )
            items = page.get("items")
            if not isinstance(items, list):
                raise DashboardHttpError(
                    "kanban_card_page_contract_invalid",
                    status_code=502,
                )
            for card in items:
                if not isinstance(card, Mapping):
                    raise DashboardHttpError(
                        "kanban_card_contract_invalid",
                        status_code=502,
                    )
                column_id = str(card.get("column_id") or "")
                column = by_id.get(column_id)
                if column is None:
                    raise DashboardHttpError(
                        "kanban_card_column_unknown",
                        status_code=502,
                    )
                assignee = card.get("assignee")
                assignee_id = (
                    str(assignee.get("id") or "")
                    if isinstance(assignee, Mapping)
                    else ""
                )
                column["tasks"].append(
                    {
                        "id": str(card.get("id") or ""),
                        "title": str(card.get("title") or card.get("id") or ""),
                        "description": str(card.get("description") or ""),
                        "status": str(card.get("status") or ""),
                        "priority": str(card.get("priority") or ""),
                        "assignee_id": assignee_id,
                        "labels": list(card.get("labels") or []),
                        "blocked": bool(card.get("blocked")),
                        "dependencies": list(card.get("dependencies") or []),
                        "revision": card.get("revision"),
                    }
                )
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                break
            cursor = str(next_cursor)
            if not cursor or cursor in seen_cursors:
                raise DashboardHttpError(
                    "kanban_card_cursor_invalid",
                    status_code=502,
                )
            seen_cursors.add(cursor)
        else:
            raise DashboardHttpError("kanban_card_page_limit_exceeded", status_code=502)
        return {
            "board_id": str(board.get("id") or self._board_id),
            "revision": board.get("revision"),
            "event_sequence": None,
            "columns": columns,
        }

    @staticmethod
    def _revision(value: object) -> int:
        if isinstance(value, bool):
            raise DashboardHttpError("kanban_revision_invalid", status_code=400)
        try:
            revision = int(value)
        except (TypeError, ValueError) as exc:
            raise DashboardHttpError("kanban_revision_invalid", status_code=400) from exc
        if revision < 0:
            raise DashboardHttpError("kanban_revision_invalid", status_code=400)
        return revision

    def _command_payload(self, expected_revision: object) -> dict[str, Any]:
        key = str(self._idempotency_key_factory() or "").strip()
        if not key or len(key) > 128:
            raise DashboardHttpError("kanban_idempotency_key_invalid", status_code=500)
        return {
            "schema_version": _KANBAN_SCHEMA,
            "board_id": self._board_id,
            "expected_revision": self._revision(expected_revision),
            "idempotency_key": key,
        }

    async def _command(
        self,
        task_id: str,
        command: str,
        payload: dict[str, Any],
    ) -> Mapping[str, Any]:
        await self._require_feature("tui_kanban")
        encoded_task = urllib.parse.quote(str(task_id or ""), safe="")
        if not encoded_task:
            raise DashboardHttpError("kanban_task_id_required", status_code=400)
        return self._require_mapping(
            self._data(
                await self._request(
                    "POST",
                    f"{_KANBAN_ROOT}/cards/{encoded_task}/commands/{command}",
                    payload=payload,
                )
            ),
            code="kanban_command_response_invalid",
        )

    async def move_task(
        self,
        task_id: str,
        *,
        target_status: str,
        target_position: int | None,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        columns = {
            "todo": "todo",
            "in_progress": "in_progress",
            "blocked": "blocked",
            "done": "completed",
            "completed": "completed",
        }
        column_id = columns.get(str(target_status or "").strip().lower())
        if column_id is None:
            raise DashboardHttpError("kanban_target_status_invalid", status_code=400)
        payload = self._command_payload(expected_revision)
        payload.update(
            {
                "column_id": column_id,
                "position": max(0, int(target_position or 0)),
            }
        )
        return await self._command(task_id, "move", payload)

    async def assign_task(
        self,
        task_id: str,
        *,
        assignee_id: str,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        payload = self._command_payload(expected_revision)
        payload["assignee_id"] = str(assignee_id or "").strip() or None
        return await self._command(task_id, "assign", payload)

    async def comment_task(
        self,
        task_id: str,
        *,
        body: str,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        payload = self._command_payload(expected_revision)
        payload["body"] = str(body or "")
        return await self._command(task_id, "comment", payload)

    async def block_task(
        self,
        task_id: str,
        *,
        reason: str,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        payload = self._command_payload(expected_revision)
        payload.update({"reason": str(reason or ""), "dependencies": []})
        return await self._command(task_id, "block", payload)

    async def complete_task(
        self,
        task_id: str,
        *,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        return await self._command(
            task_id,
            "complete",
            self._command_payload(expected_revision),
        )

    @staticmethod
    def _catalog_revision(data: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _map_catalog(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        if data.get("schema") == _MODEL_CATALOG_V2_SCHEMA:
            return self._map_catalog_v2(data)
        if data.get("schema") != _MODEL_CATALOG_SCHEMA:
            raise DashboardHttpError("model_catalog_contract_invalid", status_code=502)
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise DashboardHttpError("model_catalog_contract_invalid", status_code=502)
        revision = self._catalog_revision(data)
        providers: dict[str, list[str]] = {}
        models: list[dict[str, Any]] = []
        for model in raw_models:
            if not isinstance(model, Mapping):
                raise DashboardHttpError("model_summary_contract_invalid", status_code=502)
            provider_id = str(model.get("provider_id") or "")
            model_id = str(model.get("model_id") or "")
            if not provider_id or not model_id:
                raise DashboardHttpError("model_summary_contract_invalid", status_code=502)
            providers.setdefault(model_id, []).append(provider_id)
            models.append(
                {
                    "id": model_id,
                    "provider": provider_id,
                    "runtime": str(model.get("runtime") or "unknown"),
                    "available": model.get("availability") == "available",
                    "healthy": model.get("health") == "healthy",
                    "loaded": model.get("loaded"),
                    "context_window": model.get("context_window"),
                    "quantization": model.get("quantization"),
                    "capabilities": list(model.get("capabilities") or []),
                    "default": bool(model.get("is_default")),
                    "revision": revision,
                }
            )
        failures = data.get("provider_failures")
        provider_errors = (
            [
                {
                    "provider": str(item.get("provider_id") or ""),
                    "code": str(item.get("reason_code") or ""),
                }
                for item in failures
                if isinstance(item, Mapping)
            ]
            if isinstance(failures, list)
            else []
        )
        self._model_revision = revision
        self._model_providers = {
            model_id: tuple(sorted(set(provider_ids)))
            for model_id, provider_ids in providers.items()
        }
        return {
            "revision": revision,
            "models": models,
            "provider_errors": provider_errors,
        }

    def _map_catalog_v2(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise DashboardHttpError("model_catalog_contract_invalid", status_code=502)
        revision = str(data.get("catalog_revision") or self._catalog_revision(data))
        providers: dict[str, list[str]] = {}
        models: list[dict[str, Any]] = []
        for model in raw_models:
            if not isinstance(model, Mapping):
                raise DashboardHttpError("model_inventory_contract_invalid", status_code=502)
            provider_id = str(model.get("provider_id") or "")
            model_id = str(model.get("model_id") or "")
            claims = model.get("capabilities")
            facts = model.get("metadata_facts")
            if not provider_id or not model_id or not isinstance(claims, list):
                raise DashboardHttpError("model_inventory_contract_invalid", status_code=502)
            providers.setdefault(model_id, []).append(provider_id)
            supported = [
                str(item.get("capability_id") or "")
                for item in claims
                if isinstance(item, Mapping) and item.get("value") == "supported"
            ]
            capability_sources = [
                f"{str(item.get('fact_id') or '')}:{str(item.get('value') or '')}"
                for item in (facts or ())
                if isinstance(item, Mapping)
                and str(item.get("fact_id") or "").startswith("capability.")
            ]
            models.append(
                {
                    "id": model_id,
                    "provider": provider_id,
                    "runtime": str(model.get("runtime") or "unknown"),
                    "available": model.get("availability") == "available",
                    "healthy": model.get("health") == "healthy",
                    "loaded": model.get("loaded"),
                    "context_window": model.get("context_window"),
                    "quantization": model.get("quantization"),
                    "capabilities": supported,
                    "capability_sources": capability_sources,
                    "conflicts": [str(item) for item in list(model.get("conflicts") or ())[:50]],
                    "default": False,
                    "revision": revision,
                }
            )
        sources = data.get("sources")
        provider_errors = [
            {
                "provider": str(item.get("source_id") or ""),
                "code": str(item.get("reason_code") or item.get("status") or "degraded"),
            }
            for item in (sources or ())
            if isinstance(item, Mapping) and item.get("status") not in {"healthy", "unknown"}
        ]
        self._model_revision = revision
        self._model_providers = {
            model_id: tuple(sorted(set(provider_ids)))
            for model_id, provider_ids in providers.items()
        }
        return {"revision": revision, "models": models, "provider_errors": provider_errors}

    async def fetch_catalog(self) -> Mapping[str, Any]:
        await self._require_feature("tui_model_menu")
        try:
            response = await self._request("GET", _MODEL_CATALOG_V2_PATH)
        except DashboardHttpError as exc:
            if exc.status_code not in {403, 404}:
                raise
            response = await self._request("GET", _MODEL_CATALOG_PATH)
        data = self._require_mapping(self._data(response), code="model_catalog_contract_invalid")
        return self._map_catalog(data)

    async def refresh_catalog(self) -> Mapping[str, Any]:
        await self._require_feature("tui_model_menu")
        try:
            response = await self._request("POST", _MODEL_REFRESH_V2_PATH, payload={})
        except DashboardHttpError as exc:
            if exc.status_code not in {403, 404}:
                raise
            response = await self._request("POST", _MODEL_REFRESH_PATH, payload={})
        data = self._require_mapping(self._data(response), code="model_catalog_contract_invalid")
        return self._map_catalog(data)

    async def set_default(
        self,
        model_id: str,
        *,
        expected_revision: object,
    ) -> Mapping[str, Any]:
        current = await self.fetch_catalog()
        if str(expected_revision or "") != str(current.get("revision") or ""):
            raise RevisionConflict(
                "model_catalog_revision_conflict",
                current_revision=current.get("revision"),
            )
        providers = self._model_providers.get(str(model_id or ""), ())
        if len(providers) != 1:
            raise DashboardHttpError(
                "model_default_selection_ambiguous",
                status_code=409,
            )
        payload = {
            "schema": _MODEL_DEFAULT_COMMAND_SCHEMA,
            "provider_id": providers[0],
            "model_id": str(model_id),
        }
        return self._require_mapping(
            self._data(
                await self._request(
                    "POST",
                    _MODEL_DEFAULT_PATH,
                    payload=payload,
                )
            ),
            code="model_default_response_invalid",
        )


def build_dashboard_controller(
    *,
    endpoint: str,
    token: str,
    local_flags: DashboardFeatureFlags | None = None,
) -> DashboardSurfaceController:
    flags = local_flags or DashboardFeatureFlags.from_mapping()
    adapter = DashboardHubAdapter(
        endpoint=endpoint,
        token=token,
        local_flags=flags,
    )
    return DashboardSurfaceController(
        kanban_port=adapter,
        model_catalog_port=adapter,
        flags=flags,
    )


__all__ = [
    "DashboardHttpError",
    "DashboardHubAdapter",
    "DashboardPermissionError",
    "build_dashboard_controller",
]

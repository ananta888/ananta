"""Canonical CodeCompass retrieval entry for agents, MCP and n8n.

The service plans signal mix, intersects server-side capability with the
request, collects engine results through injectable ports, and merges
them with the existing ``HybridRetrievalService``. Qdrant stays behind
the vector port and never appears in the public envelope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.codecompass.domain_scope import is_path_within
from agent.services.codecompass_agentic_retrieval_contract import (
    MODE_AUTO,
    REASON_EMPTY_SCOPE,
    REASON_EXACT_UNAVAILABLE,
    REASON_GRAPH_UNAVAILABLE,
    REASON_INVALID_CONTINUATION,
    REASON_NO_RESULT,
    REASON_SCOPE_WIDENING,
    REASON_VECTOR_FAIL_CLOSED,
    REASON_VECTOR_STALE,
    REASON_VECTOR_UNAVAILABLE,
    SCHEMA_ID,
    SIGNAL_EXACT,
    SIGNAL_GRAPH,
    SIGNAL_VECTOR,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    AgenticRetrievalContractError,
    channel_to_signal,
    empty_response,
    map_vector_backend_reason,
    request_from_tool_args,
    signal_to_channel,
    validate_request,
)
from agent.services.codecompass_agentic_retrieval_planner import (
    CodeCompassAgenticRetrievalPlanner,
    plan_from_request,
)
from worker.retrieval.retrieval_service import HybridRetrievalService
from worker.retrieval.vector_store_contract import VectorStoreError, VectorStoreFailClosedError

ChannelSearch = Callable[..., list[dict[str, Any]]]
IndexStateFn = Callable[[], Mapping[str, Any]]
GraphSearch = Callable[..., list[dict[str, Any]]]
TokenEstimator = Callable[[str], int]
_PROCESS_CONTINUATION_SECRET = secrets.token_bytes(32)


def _default_token_estimator(value: str) -> int:
    return (len(value) + 3) // 4


def _secret_free(value: Any) -> str:
    text = str(value or "")
    lowered = text.lower()
    if any(token in lowered for token in ("authorization", "api_key", "secret", "password", "bearer ")):
        return ""
    return text


def _normalize_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _path_allowed(path: str, allowed_paths: list[str]) -> bool:
    if not allowed_paths:
        return True
    candidate = _normalize_path(path)
    if not candidate:
        return False
    return is_path_within(candidate, allowed_paths)


def _intersect_paths(capability_paths: list[str], requested_paths: list[str]) -> list[str]:
    if not capability_paths:
        return list(requested_paths)
    if not requested_paths:
        return list(capability_paths)
    narrowed: list[str] = []
    for path in requested_paths:
        if _path_allowed(path, capability_paths) and path not in narrowed:
            narrowed.append(path)
    return narrowed


class CodeCompassAgenticRetrievalService:
    """Single agent-facing retrieval facade over existing hybrid engines."""

    def __init__(
        self,
        *,
        planner: CodeCompassAgenticRetrievalPlanner | None = None,
        hybrid_service: HybridRetrievalService | None = None,
        exact_search: ChannelSearch | None = None,
        vector_search: ChannelSearch | None = None,
        graph_search: GraphSearch | None = None,
        index_state: IndexStateFn | None = None,
        continuation_secret: bytes | None = None,
        continuation_ttl_seconds: int = 900,
        token_estimator: TokenEstimator | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._planner = planner or CodeCompassAgenticRetrievalPlanner()
        self._hybrid = hybrid_service or HybridRetrievalService()
        self._exact_search = exact_search
        self._vector_search = vector_search
        self._graph_search = graph_search
        self._index_state = index_state
        self._continuation_secret = continuation_secret or _PROCESS_CONTINUATION_SECRET
        self._continuation_ttl_seconds = max(1, min(int(continuation_ttl_seconds), 3600))
        self._token_estimator = token_estimator or _default_token_estimator
        self._clock = clock or time.time

    def retrieve(
        self,
        payload: Mapping[str, Any] | None,
        *,
        capability: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from agent.services.codecompass_authority_policy import contains_client_authority

        if contains_client_authority(payload):
            return empty_response(
                query=str((payload or {}).get("query") or ""),
                status=STATUS_ERROR,
                reason_code="client_authority_forbidden",
            )
        try:
            request = validate_request(payload)
        except AgenticRetrievalContractError as exc:
            return empty_response(
                query=str((payload or {}).get("query") or ""),
                status=STATUS_ERROR,
                reason_code=exc.reason,
            )
        return self._retrieve_validated(request, capability=capability)

    def retrieve_from_tool_args(
        self,
        arguments: Mapping[str, Any] | None,
        *,
        capability: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from agent.services.codecompass_authority_policy import contains_client_authority

        if contains_client_authority(arguments):
            return empty_response(
                query=str((arguments or {}).get("query") or ""),
                status=STATUS_ERROR,
                reason_code="client_authority_forbidden",
            )
        try:
            request = request_from_tool_args(arguments)
        except AgenticRetrievalContractError as exc:
            return empty_response(
                query=str((arguments or {}).get("query") or ""),
                status=STATUS_ERROR,
                reason_code=exc.reason,
            )
        return self._retrieve_validated(request, capability=capability)

    def _retrieve_validated(
        self,
        request: dict[str, Any],
        *,
        capability: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            scope = self._resolve_scope(request["scope"], capability)
        except AgenticRetrievalContractError as exc:
            return empty_response(
                query=request["query"],
                status=STATUS_ERROR,
                reason_code=exc.reason,
                diagnostics={"scope": {"applied": True}},
            )

        allowed_signals = list((capability or {}).get("allowed_signals") or []) or None
        try:
            plan = self._planner.plan(
                query=request["query"],
                mode=request["mode"],
                requested_signals=request["requested_signals"],
                allowed_signals=allowed_signals,
                task_kind=request["task_kind"],
            )
        except AgenticRetrievalContractError as exc:
            return empty_response(
                query=request["query"],
                status=STATUS_ERROR,
                reason_code=exc.reason,
                diagnostics={"scope": self._scope_diag(scope)},
            )

        index_diag = self._safe_index_state()
        engines: dict[str, dict[str, Any]] = {}
        channel_results: dict[str, list[dict[str, Any]]] = {}
        channel_errors: dict[str, str] = {}
        warnings: list[str] = []
        fallback_reason = ""

        for signal in plan["signals"]:
            if signal == SIGNAL_VECTOR and index_diag.get("status") == "stale":
                engines[SIGNAL_VECTOR] = {
                    "status": "degraded",
                    "reason": REASON_VECTOR_STALE,
                    "candidate_count": 0,
                    "selected_count": 0,
                    "latency_ms": 0,
                }
                channel_errors[signal_to_channel(SIGNAL_VECTOR)] = REASON_VECTOR_STALE
                warnings.append(REASON_VECTOR_STALE)
                fallback_reason = fallback_reason or REASON_VECTOR_STALE
                continue
            started = time.monotonic()
            try:
                rows = self._search_signal(signal, request=request, scope=scope)
            except AgenticRetrievalContractError as exc:
                if exc.reason == REASON_VECTOR_FAIL_CLOSED:
                    return empty_response(
                        query=request["query"],
                        status=STATUS_ERROR,
                        reason_code=exc.reason,
                        plan=plan,
                        warnings=warnings,
                        diagnostics={
                            "engines": engines,
                            "index": index_diag,
                            "scope": self._scope_diag(scope),
                            "fallback_reason": exc.reason,
                        },
                    )
                engines[signal] = {
                    "status": "degraded",
                    "reason": exc.reason,
                    "candidate_count": 0,
                    "selected_count": 0,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
                channel_errors[signal_to_channel(signal)] = exc.reason
                warnings.append(exc.reason)
                fallback_reason = fallback_reason or exc.reason
                continue
            elapsed = int((time.monotonic() - started) * 1000)
            scoped_rows = [row for row in rows if _path_allowed(str(row.get("path") or ""), scope["allowed_paths"])]
            channel = signal_to_channel(signal)
            channel_results[channel] = scoped_rows
            engines[signal] = {
                "status": "ready" if scoped_rows else "degraded",
                "reason": "ok" if scoped_rows else "channel_empty",
                "candidate_count": len(scoped_rows),
                "selected_count": 0,
                "latency_ms": elapsed,
            }

        planned_channels = [signal_to_channel(signal) for signal in plan["signals"]]
        merge = self._hybrid.retrieve(
            query=request["query"],
            pipeline_contract={
                "schema": "retrieval_pipeline_contract.v1",
                "channels": planned_channels or ["codecompass_fts"],
                "fallback_order": planned_channels or ["codecompass_fts"],
            },
            channel_results=channel_results,
            channel_errors=channel_errors,
            task_type=request["task_kind"] or "bugfix",
            profile="balanced",
            top_k=int(request["budget"]["candidate_limit"]),
        )
        selected = [item for item in list(merge.get("selected") or []) if isinstance(item, dict)]
        selected = [
            item
            for item in selected
            if _path_allowed(str(item.get("path") or ""), scope["allowed_paths"])
            and self._hit_matches_scope(item, scope)
        ]
        input_count = sum(len(rows) for rows in channel_results.values())
        merged_count = max(0, input_count - len(selected))
        try:
            evidence, truncated, continuation, budget_usage = self._budget_evidence(
                selected,
                request=request,
                scope=scope,
                plan=plan,
            )
        except AgenticRetrievalContractError as exc:
            return empty_response(
                query=request["query"],
                status=STATUS_ERROR,
                reason_code=exc.reason,
                plan=plan,
                diagnostics={"scope": self._scope_diag(scope)},
            )
        selected_by_signal: dict[str, int] = {}
        for item in evidence:
            for signal in item["signals"]:
                selected_by_signal[signal] = selected_by_signal.get(signal, 0) + 1
        for signal, diagnostic in engines.items():
            diagnostic["selected_count"] = int(selected_by_signal.get(signal) or 0)

        status = STATUS_OK
        reason_code = ""
        if not evidence:
            status = STATUS_EMPTY if not fallback_reason else STATUS_DEGRADED
            reason_code = fallback_reason or REASON_NO_RESULT
        elif fallback_reason or truncated:
            status = STATUS_DEGRADED
            reason_code = fallback_reason or "budget_exhausted"

        diagnostics = {
            "engines": engines,
            "index": index_diag,
            "budget": {
                **dict(request["budget"]),
                "returned": len(evidence),
                "truncated": truncated,
                **budget_usage,
            },
            "scope": self._scope_diag(scope),
            "dedup": {
                "input_count": input_count,
                "output_count": len(evidence),
                "merged_count": merged_count,
            },
            "fallback_reason": fallback_reason,
        }
        return {
            "schema": SCHEMA_ID,
            "kind": "response",
            "status": status,
            "reason_code": reason_code,
            "query": request["query"],
            "plan": {
                "mode": plan["mode"],
                "signals": list(plan["signals"]),
                "rationale": plan["rationale"],
            },
            "evidence": evidence,
            "truncated": truncated,
            "continuation_handle": continuation,
            "warnings": warnings,
            "diagnostics": diagnostics,
        }

    def _search_signal(
        self,
        signal: str,
        *,
        request: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        limit = int(request["budget"]["candidate_limit"])
        query = str(request["query"])
        if signal == SIGNAL_EXACT:
            search = self._exact_search or self._default_exact_search
            try:
                rows = search(query, limit=limit, scope=scope, task_kind=request.get("task_kind"))
            except AgenticRetrievalContractError:
                raise
            except Exception as exc:
                raise AgenticRetrievalContractError(REASON_EXACT_UNAVAILABLE) from exc
            return [self._normalize_hit(row, SIGNAL_EXACT) for row in list(rows or [])]
        if signal == SIGNAL_VECTOR:
            search = self._vector_search or self._default_vector_search
            try:
                rows = search(query, limit=limit, scope=scope)
            except VectorStoreFailClosedError as exc:
                raise AgenticRetrievalContractError(
                    map_vector_backend_reason(getattr(exc, "reason", REASON_VECTOR_FAIL_CLOSED))
                ) from exc
            except VectorStoreError as exc:
                raise AgenticRetrievalContractError(map_vector_backend_reason(exc.reason)) from exc
            except AgenticRetrievalContractError:
                raise
            except Exception as exc:
                reason = map_vector_backend_reason(getattr(exc, "reason", "") or str(exc))
                raise AgenticRetrievalContractError(reason) from exc
            return [self._normalize_hit(row, SIGNAL_VECTOR) for row in list(rows or [])]
        search = self._graph_search or self._default_graph_search
        try:
            rows = search(
                query,
                limit=limit,
                scope=scope,
                depth=int(request["budget"]["graph_depth"]),
            )
        except AgenticRetrievalContractError:
            raise
        except Exception as exc:
            raise AgenticRetrievalContractError(REASON_GRAPH_UNAVAILABLE) from exc
        return [self._normalize_hit(row, SIGNAL_GRAPH) for row in list(rows or [])]

    def _normalize_hit(self, row: Mapping[str, Any], signal: str) -> dict[str, Any]:
        metadata = dict(row.get("metadata") or {})
        path = _normalize_path(
            row.get("path") or row.get("source") or metadata.get("repo_relative_path") or metadata.get("file")
        )
        record_id = str(
            row.get("id")
            or row.get("record_id")
            or metadata.get("record_id")
            or row.get("content_hash")
            or path
            or ""
        )
        return {
            "path": path,
            "record_id": record_id,
            "content_hash": str(row.get("content_hash") or metadata.get("content_hash") or record_id),
            "content": str(row.get("content") or row.get("text") or row.get("excerpt") or ""),
            "score": float(row.get("score") or 0.0),
            "symbol_name": str(row.get("symbol") or row.get("symbol_name") or metadata.get("symbol") or ""),
            "metadata": {
                **metadata,
                "record_kind": str(row.get("kind") or metadata.get("record_kind") or signal),
                "source_id": str(row.get("source_id") or metadata.get("source_id") or ""),
                "source_version": str(row.get("source_version") or metadata.get("source_version") or ""),
                "tenant_id": str(row.get("tenant_id") or metadata.get("tenant_id") or ""),
                "workspace_id": str(row.get("workspace_id") or metadata.get("workspace_id") or ""),
                "repository_id": str(row.get("repository_id") or metadata.get("repository_id") or ""),
                "revision": str(
                    row.get("revision")
                    or row.get("source_revision")
                    or metadata.get("revision")
                    or metadata.get("source_revision")
                    or ""
                ),
                "source_scope": str(row.get("source_scope") or metadata.get("source_scope") or ""),
            },
            "channel": signal_to_channel(signal),
            "source": str(row.get("source") or signal),
        }

    @staticmethod
    def _hit_matches_scope(
        hit: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> bool:
        metadata = dict(hit.get("metadata") or {})
        for field in (
            "tenant_id",
            "workspace_id",
            "repository_id",
            "revision",
            "source_scope",
        ):
            observed = str(metadata.get(field) or hit.get(field) or "")
            expected = str(scope.get(field) or "")
            if observed and expected and observed != expected:
                return False
        return True

    def _budget_evidence(
        self,
        selected: list[dict[str, Any]],
        *,
        request: Mapping[str, Any],
        scope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], bool, str, dict[str, Any]]:
        top_k = int(request["budget"]["top_k"])
        max_chars = int(request["budget"]["max_chars"])
        max_tokens = int(request["budget"]["max_tokens"])
        offset = self._continuation_offset(
            request.get("continuation_handle"),
            request=request,
            scope=scope,
            plan=plan,
        )
        remaining = selected[offset:]
        evidence: list[dict[str, Any]] = []
        truncation_reasons: set[str] = set()

        def usage(rows: list[dict[str, Any]]) -> tuple[int, int]:
            serialized = json.dumps(
                rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return len(serialized), max(0, int(self._token_estimator(serialized)))

        def fits(rows: list[dict[str, Any]]) -> bool:
            chars, tokens = usage(rows)
            return chars <= max_chars and tokens <= max_tokens

        for item in remaining:
            if len(evidence) >= top_k:
                truncation_reasons.add("top_k")
                break
            excerpt = str(item.get("content") or "")
            signals = self._signals_for_item(item)
            metadata = dict(item.get("metadata") or {})
            line_start = item.get("line_start") or metadata.get("line_start") or metadata.get("start_line")
            line_end = item.get("line_end") or metadata.get("line_end") or metadata.get("end_line")
            entry = {
                "id": str(item.get("record_id") or item.get("path") or f"hit-{len(evidence)}"),
                "path": str(item.get("path") or ""),
                "revision": str(scope.get("revision") or ""),
                "signal_type": signals[0],
                "signals": signals,
                "score": float(item.get("final_score") or item.get("score") or 0.0),
                "score_breakdown": dict(item.get("channel_contributions") or {}),
                "excerpt": excerpt,
                "symbol": str(item.get("symbol_name") or ""),
                "kind": str(metadata.get("record_kind") or signals[0]),
                "verification_status": (
                    "verified" if metadata.get("source_id_verified") else "unverified"
                ),
                "source": str(item.get("channel") or item.get("source") or signals[0]),
                "truncated": False,
            }
            try:
                if line_start is not None:
                    entry["line_start"] = int(line_start)
                if line_end is not None:
                    entry["line_end"] = int(line_end)
            except (TypeError, ValueError):
                pass
            candidate = [*evidence, entry]
            if fits(candidate):
                evidence.append(entry)
                continue

            candidate_chars, candidate_tokens = usage(candidate)
            if candidate_chars > max_chars:
                truncation_reasons.add("max_chars")
            if candidate_tokens > max_tokens:
                truncation_reasons.add("max_tokens")

            marker = "\n[truncated]"
            low = 0
            high = len(excerpt)
            best: dict[str, Any] | None = None
            while low <= high:
                midpoint = (low + high) // 2
                shortened = dict(entry)
                shortened["excerpt"] = excerpt[:midpoint].rstrip() + marker
                shortened["truncated"] = True
                if fits([*evidence, shortened]):
                    best = shortened
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            if best is not None:
                evidence.append(best)
            break
        consumed = offset + len(evidence)
        truncated = consumed < len(selected) or any(item.get("truncated") for item in evidence)
        continuation = ""
        if consumed < len(selected):
            continuation = self._encode_continuation(
                consumed,
                request=request,
                scope=scope,
                plan=plan,
            )
        used_chars, used_tokens = usage(evidence)
        return evidence, truncated, continuation, {
            "used_chars": used_chars,
            "used_tokens": used_tokens,
            "token_estimator": getattr(self._token_estimator, "__name__", "injected"),
            "truncation_reason": "+".join(sorted(truncation_reasons)),
        }

    @staticmethod
    def _signals_for_item(item: Mapping[str, Any]) -> list[str]:
        contributions = dict(item.get("channel_contributions") or {})
        signals: list[str] = []
        if contributions:
            ranked = sorted(contributions.items(), key=lambda pair: float(pair[1] or 0.0), reverse=True)
            for channel, _score in ranked:
                signal = channel_to_signal(str(channel))
                if signal not in signals:
                    signals.append(signal)
        primary = channel_to_signal(str(item.get("channel") or SIGNAL_EXACT))
        if primary not in signals:
            signals.insert(0, primary)
        return signals or [SIGNAL_EXACT]

    def _continuation_binding(
        self,
        *,
        request: Mapping[str, Any],
        scope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> str:
        payload = {
            "query": str(request.get("query") or ""),
            "scope": dict(scope),
            "budget": dict(request.get("budget") or {}),
            "plan": {
                "mode": str(plan.get("mode") or ""),
                "signals": list(plan.get("signals") or []),
            },
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _encode_continuation(
        self,
        offset: int,
        *,
        request: Mapping[str, Any],
        scope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> str:
        issued_at = int(self._clock())
        payload = json.dumps(
            {
                "offset": int(offset),
                "schema": SCHEMA_ID,
                "issued_at_epoch": issued_at,
                "expires_at_epoch": issued_at + self._continuation_ttl_seconds,
                "binding": self._continuation_binding(
                    request=request,
                    scope=scope,
                    plan=plan,
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(
            self._continuation_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}.{signature}"

    def _continuation_offset(
        self,
        handle: Any,
        *,
        request: Mapping[str, Any],
        scope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> int:
        raw = str(handle or "").strip()
        if not raw:
            return 0
        try:
            encoded, signature = raw.split(".", 1)
            expected = hmac.new(
                self._continuation_secret,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padded = encoded + "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if payload.get("schema") != SCHEMA_ID:
                raise ValueError
            now = int(self._clock())
            if int(payload.get("issued_at_epoch") or 0) <= 0:
                raise ValueError
            if int(payload.get("expires_at_epoch") or 0) <= now:
                raise ValueError
            if payload.get("binding") != self._continuation_binding(
                request=request,
                scope=scope,
                plan=plan,
            ):
                raise ValueError
            offset = int(payload.get("offset"))
            if offset < 0 or offset > int(request["budget"]["candidate_limit"]):
                raise ValueError
            return offset
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            raise AgenticRetrievalContractError(REASON_INVALID_CONTINUATION) from None

    def _resolve_scope(
        self,
        requested: Mapping[str, Any],
        capability: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        requested_scope = {
            "tenant_id": str(requested.get("tenant_id") or ""),
            "workspace_id": str(requested.get("workspace_id") or ""),
            "repository_id": str(requested.get("repository_id") or ""),
            "source_scope": str(requested.get("source_scope") or ""),
            "revision": str(requested.get("revision") or ""),
            "allowed_paths": list(requested.get("allowed_paths") or []),
        }
        if capability is None:
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE)

        from agent.services.codecompass_retrieval_capability_service import (
            verify_retrieval_capability,
        )

        try:
            capability = verify_retrieval_capability(capability, now_epoch=self._clock())
        except (TypeError, ValueError):
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE) from None

        bound = {
            "tenant_id": str(capability.get("tenant_id") or ""),
            "workspace_id": str(capability.get("workspace_id") or ""),
            "repository_id": str(capability.get("repository_id") or ""),
            "source_scope": str(capability.get("source_scope") or ""),
            "revision": str(capability.get("revision") or ""),
            "allowed_paths": [
                _normalize_path(item)
                for item in list(capability.get("allowed_paths") or [])
                if _normalize_path(item)
            ],
            "allowed_index_ids": tuple(
                sorted(
                    {
                        str(item).strip()
                        for item in list(capability.get("allowed_index_ids") or [])
                        if str(item).strip()
                    }
                )
            ),
            "subject_id": str(capability.get("subject_id") or ""),
            "capability_digest": str(capability.get("capability_digest") or ""),
            "expires_at_epoch": int(capability.get("expires_at_epoch") or 0),
        }
        if (
            not bound["tenant_id"]
            or not bound["workspace_id"]
            or not bound["repository_id"]
            or not bound["source_scope"]
            or not bound["revision"]
        ):
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE)
        if not bound["allowed_paths"]:
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE)

        for field in ("tenant_id", "workspace_id", "repository_id", "revision", "source_scope"):
            requested_value = requested_scope.get(field) or ""
            bound_value = bound.get(field) or ""
            if requested_value and bound_value and requested_value != bound_value:
                raise AgenticRetrievalContractError(REASON_SCOPE_WIDENING)

        narrowed = _intersect_paths(bound["allowed_paths"], requested_scope["allowed_paths"])
        if not narrowed:
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE)
        return {
            "tenant_id": bound["tenant_id"] or requested_scope["tenant_id"],
            "workspace_id": bound["workspace_id"],
            "repository_id": bound["repository_id"] or requested_scope["repository_id"],
            "source_scope": bound["source_scope"] or requested_scope["source_scope"],
            "revision": bound["revision"],
            "allowed_paths": narrowed,
            "allowed_index_ids": bound["allowed_index_ids"],
            "subject_id": bound["subject_id"],
            "capability_digest": bound["capability_digest"],
            "expires_at_epoch": bound["expires_at_epoch"],
        }

    @staticmethod
    def _scope_diag(scope: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "applied": True,
            "tenant_id": str(scope.get("tenant_id") or ""),
            "workspace_id": str(scope.get("workspace_id") or ""),
            "revision": str(scope.get("revision") or ""),
            "allowed_path_count": len(list(scope.get("allowed_paths") or [])),
        }

    def _safe_index_state(self) -> dict[str, Any]:
        loader = self._index_state or self._default_index_state
        try:
            raw = dict(loader() or {})
        except Exception:
            return {"status": "unavailable", "reason": REASON_VECTOR_UNAVAILABLE}
        return {
            "status": str(raw.get("status") or "ready"),
            "reason": str(raw.get("reason") or ""),
            "manifest_hash": _secret_free(raw.get("manifest_hash")),
            "model": _secret_free(raw.get("model")),
            "dimensions": int(raw.get("dimensions") or 0),
            "embedding_text_profile": _secret_free(raw.get("embedding_text_profile")),
        }

    def _default_exact_search(
        self,
        query: str,
        *,
        limit: int,
        scope: Mapping[str, Any],
        task_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        from agent.services.knowledge_index_retrieval_service import (
            get_knowledge_index_retrieval_service,
        )

        source_scope = str(scope.get("source_scope") or "").strip()
        if not source_scope:
            raise AgenticRetrievalContractError(REASON_EMPTY_SCOPE)
        source_scopes = {source_scope}
        allowed_index_ids = {
            str(item)
            for item in list(scope.get("allowed_index_ids") or [])
            if str(item).strip()
        } or None
        return get_knowledge_index_retrieval_service().search_records(
            query,
            limit=limit,
            task_kind=task_kind or None,
            source_scopes=source_scopes,
            allowed_index_ids=allowed_index_ids,
            authoritative_scope=scope,
        )

    def _default_vector_search(
        self,
        query: str,
        *,
        limit: int,
        scope: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        # Unconfigured vector backend stays silent in auto/hybrid so exact
        # repository evidence remains available. Explicit vector mode still
        # surfaces an empty vector channel in diagnostics.
        return []

    def _default_graph_search(
        self,
        query: str,
        *,
        limit: int,
        scope: Mapping[str, Any],
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        from agent.services.codecompass_scoped_graph_search_service import (
            get_codecompass_scoped_graph_search_service,
        )

        return get_codecompass_scoped_graph_search_service().search(
            query,
            limit=limit,
            scope=scope,
            depth=depth,
        )

    def _default_index_state(self) -> dict[str, Any]:
        from agent.services.codecompass_agentic_index_state import load_agentic_index_state

        return load_agentic_index_state()


_agentic_retrieval_service = CodeCompassAgenticRetrievalService()


def get_codecompass_agentic_retrieval_service() -> CodeCompassAgenticRetrievalService:
    return _agentic_retrieval_service


def plan_agentic_retrieval(
    *,
    query: str,
    mode: str = MODE_AUTO,
    requested_signals: list[str] | None = None,
    allowed_signals: list[str] | None = None,
    task_kind: str | None = None,
) -> dict[str, Any]:
    return plan_from_request(
        {
            "query": query,
            "mode": mode,
            "requested_signals": requested_signals or [],
            "task_kind": task_kind or "",
        },
        allowed_signals=allowed_signals,
    )

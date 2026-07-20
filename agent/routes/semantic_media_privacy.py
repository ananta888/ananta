"""Dedicated privacy workflow for semantic-media audit export and erasure."""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_user_auth
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
    SpeechDatasetManifestError,
)
from agent.services.semantic_media_audit_lifecycle_service import (
    SemanticMediaAuditLifecyclePrincipal,
    SemanticMediaAuditLifecycleService,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditError,
    SemanticMediaAuditRecorder,
)
from agent.services.speech_dataset_privacy_preview_service import (
    SpeechDatasetPrivacyPreviewError,
    SpeechDatasetPrivacyPreviewService,
)
from agent.services.voice_governance_domain import VoicePrincipal

semantic_media_privacy_bp = Blueprint("semantic_media_privacy", __name__)


class SpeechDatasetPreviewArtifactPort(Protocol):
    """Resolve Hub-owned preview refs after a separate grant was supplied."""

    def refs_for_manifest(
        self,
        principal: VoicePrincipal,
        *,
        manifest_digest: str,
    ) -> Mapping[str, str]: ...


@runtime_checkable
class SpeechDatasetManifestReaderPort(Protocol):
    def get_by_digest(
        self,
        principal: VoicePrincipal,
        digest: str,
    ) -> dict[str, object] | None: ...


class SpeechDatasetAdmissionFindingPort(Protocol):
    def findings_for_manifest(
        self,
        principal: VoicePrincipal,
        *,
        manifest_digest: str,
    ) -> Mapping[str, Mapping[str, object]]: ...


@semantic_media_privacy_bp.get("/v1/semantic-media/privacy/audit-export")
@check_user_auth
def export_semantic_media_audit():
    try:
        lifecycle, recorder, principal = _context()
        scope_digest = recorder.digest("scope", _logical_scope(request.args.get("scope")))
        result = lifecycle.export_scope(principal, scope_digest=scope_digest)
        return jsonify({"ok": True, "data": result}), 200
    except SemanticMediaAuditError as exc:
        return _error(exc)


@semantic_media_privacy_bp.delete("/v1/semantic-media/privacy/audit-scope")
@check_user_auth
def erase_semantic_media_audit_scope():
    try:
        lifecycle, recorder, principal = _context()
        body = request.get_json(silent=True) or {}
        if set(body) != {"scope"}:
            raise SemanticMediaAuditError("semantic_audit_scope_invalid", status_code=400)
        scope_digest = recorder.digest("scope", _logical_scope(body.get("scope")))
        deleted = lifecycle.erase_scope(principal, scope_digest=scope_digest)
        return jsonify({"ok": True, "data": {"deleted_event_count": deleted}}), 200
    except SemanticMediaAuditError as exc:
        return _error(exc)


@semantic_media_privacy_bp.delete("/v1/semantic-media/privacy/audit-tenant")
@check_user_auth
def erase_semantic_media_tenant_audit():
    try:
        lifecycle, _recorder, principal = _context()
        if request.get_data(cache=True, as_text=True).strip() not in {"", "{}"}:
            raise SemanticMediaAuditError("semantic_audit_erasure_request_invalid", status_code=400)
        deleted = lifecycle.erase_tenant(principal)
        return jsonify({"ok": True, "data": {"deleted_event_count": deleted}}), 200
    except SemanticMediaAuditError as exc:
        return _error(exc)


@semantic_media_privacy_bp.get(
    "/v1/semantic-media/privacy/speech-datasets/<manifest_digest>/preview"
)
@check_user_auth
def preview_speech_dataset(manifest_digest: str):
    """Return a bounded aggregate preview; raw refs require a separate grant."""

    try:
        if set(request.args) - {"include_raw_audio"}:
            raise SpeechDatasetPrivacyPreviewError(
                "speech_preview_query_invalid",
                status_code=400,
            )
        digest = _digest(manifest_digest)
        principal = _voice_principal()
        manifest = _speech_dataset_reader().get_by_digest(principal, digest)
        if manifest is None:
            raise SpeechDatasetPrivacyPreviewError(
                "speech_preview_dataset_not_found",
                status_code=404,
            )
        include_raw_audio = _query_boolean(request.args.get("include_raw_audio", "false"))
        grant_ref = str(request.headers.get("X-Speech-Preview-Grant") or "").strip()
        if grant_ref and (
            not 8 <= len(grant_ref) <= 256
            or any(character.isspace() for character in grant_ref)
        ):
            raise SpeechDatasetPrivacyPreviewError(
                "speech_preview_raw_audio_grant_invalid",
                status_code=400,
            )
        raw_refs: Mapping[str, str] = {}
        if include_raw_audio:
            port = current_app.extensions.get("speech_dataset_preview_artifact_port")
            resolver = getattr(port, "refs_for_manifest", None)
            if callable(resolver):
                raw_refs = dict(resolver(principal, manifest_digest=digest))
        findings: Mapping[str, Mapping[str, object]] = {}
        finding_port = current_app.extensions.get("speech_dataset_admission_finding_port")
        finding_resolver = getattr(finding_port, "findings_for_manifest", None)
        if callable(finding_resolver):
            findings = dict(finding_resolver(principal, manifest_digest=digest))
        preview = _speech_preview_service().preview(
            principal,
            manifest,
            admission_findings=findings,
            include_raw_audio=include_raw_audio,
            raw_audio_preview_grant_ref=grant_ref or None,
            raw_audio_refs=raw_refs,
        )
        return jsonify({"ok": True, "data": preview}), 200
    except (SpeechDatasetPrivacyPreviewError, SpeechDatasetManifestError) as exc:
        return _speech_preview_error(exc)


def _context() -> tuple[
    SemanticMediaAuditLifecycleService,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditLifecyclePrincipal,
]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or "").strip()
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    if not tenant or not subject:
        raise SemanticMediaAuditError("semantic_audit_unauthenticated", status_code=401)
    recorder = current_app.extensions.get("semantic_media_audit_recorder")
    lifecycle = current_app.extensions.get("semantic_media_audit_lifecycle_service")
    if not isinstance(recorder, SemanticMediaAuditRecorder) or not isinstance(
        lifecycle, SemanticMediaAuditLifecycleService
    ):
        raise SemanticMediaAuditError("semantic_audit_lifecycle_unavailable", status_code=503)
    return (
        lifecycle,
        recorder,
        SemanticMediaAuditLifecyclePrincipal(
            tenant_digest=recorder.digest("tenant", tenant),
            subject_digest=recorder.digest("subject", subject),
            roles=frozenset(_roles(identity)),
        ),
    )


def _voice_principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject or not tenant:
        raise SpeechDatasetPrivacyPreviewError(
            "speech_preview_unauthenticated",
            status_code=401,
        )
    return VoicePrincipal(tenant, subject)


def _speech_dataset_reader() -> SpeechDatasetManifestReaderPort:
    service = current_app.extensions.get("speech_dataset_manifest_reader")
    if isinstance(service, SpeechDatasetManifestReaderPort):
        return service
    service = MlInternSpeechDatasetBuildService()
    current_app.extensions["speech_dataset_manifest_reader"] = service
    return service


def _speech_preview_service() -> SpeechDatasetPrivacyPreviewService:
    service = current_app.extensions.get("speech_dataset_privacy_preview_service")
    if isinstance(service, SpeechDatasetPrivacyPreviewService):
        return service
    service = SpeechDatasetPrivacyPreviewService()
    current_app.extensions["speech_dataset_privacy_preview_service"] = service
    return service


def _query_boolean(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise SpeechDatasetPrivacyPreviewError(
        "speech_preview_query_invalid",
        status_code=400,
    )


def _digest(value: object) -> str:
    rendered = str(value or "")
    if len(rendered) != 64 or any(character not in "0123456789abcdef" for character in rendered):
        raise SpeechDatasetPrivacyPreviewError(
            "speech_preview_manifest_digest_invalid",
            status_code=400,
        )
    return rendered


def _logical_scope(value: object) -> str:
    scope = str(value or "")
    if (
        not 8 <= len(scope) <= 256
        or any(character.isspace() for character in scope)
        or not scope.startswith(("semantic-contract:", "semantic-media-session:", "speech-job:"))
    ):
        raise SemanticMediaAuditError("semantic_audit_scope_invalid", status_code=400)
    return scope


def _roles(identity: dict) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(identity.get("role"), str):
        values.append(str(identity["role"]))
    if isinstance(identity.get("roles"), list):
        values.extend(str(value) for value in identity["roles"])
    realm = identity.get("realm_access")
    if isinstance(realm, dict) and isinstance(realm.get("roles"), list):
        values.extend(str(value) for value in realm["roles"])
    return tuple(values)


def _error(exc: SemanticMediaAuditError):
    return jsonify({"ok": False, "error": {"code": exc.reason_code, "retriable": False}}), exc.status_code


def _speech_preview_error(exc: SpeechDatasetPrivacyPreviewError | SpeechDatasetManifestError):
    return jsonify(
        {
            "ok": False,
            "error": {
                "code": exc.reason_code,
                "retriable": exc.status_code >= 500,
            },
        }
    ), exc.status_code


__all__ = [
    "SpeechDatasetAdmissionFindingPort",
    "SpeechDatasetManifestReaderPort",
    "SpeechDatasetPreviewArtifactPort",
    "semantic_media_privacy_bp",
]

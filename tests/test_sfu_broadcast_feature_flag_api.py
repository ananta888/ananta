from __future__ import annotations

import time
from dataclasses import replace

import jwt
from flask import Flask

from agent.config import settings
from agent.repositories.sfu_broadcast_feature_flag_repository import (
    InMemorySfuBroadcastFeatureFlagRepository,
    InMemorySfuBroadcastFeatureFlagStore,
    SfuBroadcastFeatureFlagMutationResult,
)
from agent.routes import network_profiles
from agent.routes.admin.sfu_broadcast_feature_flags import (
    sfu_broadcast_feature_flags_bp,
)
from agent.services.sfu_broadcast_feature_policy import (
    SFB_BROADCAST_FEATURE_KEYS,
    SfuBroadcastFeaturePolicy,
)


def _token(
    subject: str,
    *,
    role: str,
    tenant_id: str = "tenant-a",
    region: str = "eu-central",
    room_cohort: str = "internal",
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "role": role,
            "tenant_id": tenant_id,
            "region": region,
            "room_cohort": room_cohort,
            "iat": now,
            "exp": now + 600,
        },
        settings.secret_key,
        algorithm="HS256",
    )


def _headers(subject: str = "admin-a", *, role: str = "admin", key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_token(subject, role=role)}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _body(**changes) -> dict[str, object]:
    body: dict[str, object] = {
        "tenant_id": "tenant-a",
        "region": "eu-central",
        "room_cohort": "internal",
        "enabled": True,
        "rollout_stage": "cohort",
        "expected_version": 0,
        "actor": "admin-a",
        "reason": "approved rollout",
    }
    body.update(changes)
    return body


def _app(
    repository: InMemorySfuBroadcastFeatureFlagRepository | None = None,
    *,
    static_enabled: bool = False,
) -> tuple[Flask, InMemorySfuBroadcastFeatureFlagRepository]:
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.secret_key = settings.secret_key
    app.register_blueprint(sfu_broadcast_feature_flags_bp)
    selected = repository or InMemorySfuBroadcastFeatureFlagRepository()
    app.extensions["sfu_broadcast_feature_policy"] = SfuBroadcastFeaturePolicy(
        selected,
        static_source={key: static_enabled for key in SFB_BROADCAST_FEATURE_KEYS},
    )
    return app, selected


def test_admin_rbac_idempotency_actor_reason_and_expected_version_are_mandatory() -> None:
    app, _repository = _app()
    client = app.test_client()
    path = "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast"

    assert client.put(path, json=_body(), headers=_headers(role="user", key="rbac-denied-1")).status_code == 403
    missing_key = client.put(path, json=_body(), headers=_headers())
    assert missing_key.status_code == 400
    assert missing_key.json["data"]["reason_code"] == "feature_flag_idempotency_key_invalid"
    actor_mismatch = client.put(
        path,
        json=_body(actor="somebody-else"),
        headers=_headers(key="actor-mismatch-1"),
    )
    assert actor_mismatch.status_code == 403
    missing_reason = client.put(
        path,
        json=_body(reason=""),
        headers=_headers(key="reason-missing-1"),
    )
    assert missing_reason.status_code == 400
    missing_version = client.put(
        path,
        json={key: value for key, value in _body().items() if key != "expected_version"},
        headers=_headers(key="version-missing-1"),
    )
    assert missing_version.status_code == 400


def test_unknown_cohort_cas_retry_and_rollback() -> None:
    app, _repository = _app()
    client = app.test_client()
    path = "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast"
    unknown = client.put(
        path,
        json=_body(room_cohort="invented"),
        headers=_headers(key="unknown-cohort-1"),
    )
    assert unknown.status_code == 400
    assert unknown.json["data"]["reason_code"] == "feature_flag_room_cohort_unknown"

    created = client.put(path, json=_body(), headers=_headers(key="broadcast-create-1"))
    assert created.status_code == 201
    assert created.json["data"]["mutation"]["version"] == 1
    replay = client.put(path, json=_body(), headers=_headers(key="broadcast-create-1"))
    assert replay.status_code == 200
    assert replay.json["data"]["mutation"]["status"] == "replayed"
    rollback = client.put(
        path,
        json=_body(enabled=False, rollout_stage="flag_off", expected_version=1),
        headers=_headers(key="broadcast-rollback-1"),
    )
    assert rollback.status_code == 200
    assert rollback.json["data"]["mutation"]["version"] == 2
    stale = client.put(
        path,
        json=_body(expected_version=1),
        headers=_headers(key="broadcast-stale-1"),
    )
    assert stale.status_code == 409
    conflicting_retry = client.put(
        path,
        json=_body(enabled=False),
        headers=_headers(key="broadcast-create-1"),
    )
    assert conflicting_retry.status_code == 409


def test_kill_switches_have_distinct_stable_reason_codes_and_idempotent_audit() -> None:
    app, _repository = _app(static_enabled=True)
    client = app.test_client()
    for flag in ("stop_admission", "graceful_drain", "immediate_security_fence"):
        response = client.put(
            f"/api/admin/sfu-broadcast-feature-flags/{flag}",
            json=_body(rollout_stage="security", reason=f"activate {flag}"),
            headers=_headers(key=f"kill-{flag}-1"),
        )
        assert response.status_code == 201
        mutation = response.json["data"]["mutation"]
        assert mutation["reason_code"] == f"sfu_broadcast.kill_switch.{flag}"
        assert mutation["audited_at"] > 0
        replay = client.put(
            f"/api/admin/sfu-broadcast-feature-flags/{flag}",
            json=_body(rollout_stage="security", reason=f"activate {flag}"),
            headers=_headers(key=f"kill-{flag}-1"),
        )
        assert replay.json["data"]["mutation"]["audited_at"] == mutation["audited_at"]

    effective = client.get(
        "/api/sfu-broadcast-feature-flags/effective",
        headers=_headers(role="user"),
    )
    projection = effective.json["data"]["projection"]
    assert set(projection["reason_codes"]) == {
        "sfu_broadcast.kill_switch.stop_admission",
        "sfu_broadcast.kill_switch.graceful_drain",
        "sfu_broadcast.kill_switch.immediate_security_fence",
    }
    assert not any(projection["flags"].values())


def test_effective_read_is_claim_scoped_and_rejects_idor_override() -> None:
    app, _repository = _app()
    client = app.test_client()
    created = client.put(
        "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast",
        json=_body(),
        headers=_headers(key="tenant-a-broadcast-1"),
    )
    assert created.status_code == 201
    own = client.get(
        "/api/sfu-broadcast-feature-flags/effective",
        headers=_headers(role="user"),
    )
    assert own.json["data"]["projection"]["flags"]["semantic_media_broadcast"] is True

    tenant_b = {"Authorization": f"Bearer {_token('user-b', role='user', tenant_id='tenant-b')}"}
    other = client.get("/api/sfu-broadcast-feature-flags/effective", headers=tenant_b)
    assert other.json["data"]["projection"]["flags"]["semantic_media_broadcast"] is False
    idor = client.get(
        "/api/sfu-broadcast-feature-flags/effective?tenant_id=tenant-a",
        headers=tenant_b,
    )
    assert idor.status_code == 403


def test_database_failure_and_missing_audit_fail_closed() -> None:
    repository = InMemorySfuBroadcastFeatureFlagRepository()
    app, _repository = _app(repository, static_enabled=True)
    repository.set_available(False)
    response = app.test_client().get(
        "/api/sfu-broadcast-feature-flags/effective",
        headers=_headers(role="user"),
    )
    projection = response.json["data"]["projection"]
    assert projection["available"] is False
    assert not any(projection["flags"].values())

    class _UnauditedRepository(InMemorySfuBroadcastFeatureFlagRepository):
        def create(self, mutation, *, expected_version):
            result = super().create(mutation, expected_version=expected_version)
            assert result.state is not None
            return SfuBroadcastFeatureFlagMutationResult(
                result.status,
                replace(result.state, audited_at=0),
                result.reason_code,
            )

    unaudited_app, _ = _app(_UnauditedRepository())
    rejected = unaudited_app.test_client().put(
        "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast",
        json=_body(),
        headers=_headers(key="unaudited-create-1"),
    )
    assert rejected.status_code == 503
    assert rejected.json["data"]["reason_code"] == "feature_flag_audit_missing"


def test_shared_store_models_multi_hub_cas_fencing() -> None:
    store = InMemorySfuBroadcastFeatureFlagStore()
    app_a, _ = _app(InMemorySfuBroadcastFeatureFlagRepository(store=store))
    app_b, _ = _app(InMemorySfuBroadcastFeatureFlagRepository(store=store))
    path = "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast"
    first = app_a.test_client().put(path, json=_body(), headers=_headers(key="hub-a-create-1"))
    assert first.status_code == 201
    second = app_b.test_client().put(
        path,
        json=_body(enabled=False, rollout_stage="flag_off", expected_version=1),
        headers=_headers(key="hub-b-update-1"),
    )
    assert second.status_code == 200
    stale = app_a.test_client().put(
        path,
        json=_body(expected_version=1),
        headers=_headers(key="hub-a-stale-1"),
    )
    assert stale.status_code == 409


def test_network_profile_projects_only_effective_booleans_and_version(monkeypatch) -> None:
    app, _repository = _app()
    app.register_blueprint(network_profiles.network_profiles_bp)
    monkeypatch.setattr(
        network_profiles,
        "_load_profiles",
        lambda: {
            "public-ananta": {
                "profile_id": "public-ananta",
                "label": "Test",
                "oidc": {},
                "rendezvous": {},
                "ice_servers": [],
            }
        },
    )
    client = app.test_client()
    client.put(
        "/api/admin/sfu-broadcast-feature-flags/semantic_media_broadcast",
        json=_body(),
        headers=_headers(key="network-profile-create-1"),
    )
    response = client.get(
        "/api/network-profiles/public-ananta",
        headers=_headers(role="user"),
    )
    profile = response.json["profile"]
    assert profile["semantic_media_feature_flags"]["semantic_media_broadcast"] is True
    assert profile["sfu_broadcast_feature_version"] == 1
    assert profile["sfu_broadcast_feature_available"] is True
    assert "room_cohort" not in profile
    assert "rollout_stage" not in profile


def test_bootstrap_wires_sql_repository_and_policy(app) -> None:
    assert "sfu_broadcast_feature_flag_repository" in app.extensions
    assert isinstance(app.extensions.get("sfu_broadcast_feature_policy"), SfuBroadcastFeaturePolicy)

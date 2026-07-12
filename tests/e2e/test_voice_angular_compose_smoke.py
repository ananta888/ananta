from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import pytest
import requests

pytestmark = pytest.mark.integration

_HTTP_TIMEOUT_SECONDS = 30
_BUNDLE_TIMEOUT_SECONDS = 60
_MAX_BUNDLE_FILES = 512
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_SHARED_INVALID_COMBINATION_REASON = "voice_configuration.duplicate_backend"
_EXPECTED_HUB_BUNDLE_PATHS = (
    "/v1/voice/capabilities",
    "/v1/voice/configuration",
    "/v1/voice/reviews",
    "/v1/voice/consents/",
    "/v1/voice/privacy/",
)
_FORBIDDEN_RUNTIME_BUNDLE_TARGETS = (
    ":8090",
    ":8091",
    "/internal/v1/voice",
    "/internal/v1/restricted-inference",
    "http://voice-runtime",
    "http://restricted-inference",
)


def _required(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        pytest.skip(f"explicit Angular Compose smoke input is missing: {name}")
    return value


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.netloc.lower()


def _angular_bundle(frontend_url: str) -> str:
    page = requests.get(frontend_url, timeout=_HTTP_TIMEOUT_SECONDS, allow_redirects=False)
    page.raise_for_status()
    script_paths = re.findall(r'["\']([^"\']+\.js)["\']', page.text)
    assert script_paths, "Compose frontend did not reference an Angular JavaScript bundle"
    pending = [urljoin(f"{frontend_url}/", path) for path in script_paths]
    visited: set[str] = set()
    javascript: list[str] = []
    total_bytes = 0
    while pending:
        script_url = pending.pop()
        if script_url in visited:
            continue
        assert len(visited) < _MAX_BUNDLE_FILES, "Angular bundle dependency graph exceeds the file limit"
        assert _origin(script_url) == _origin(frontend_url), "Angular bundle must stay on the Compose frontend"
        response = requests.get(script_url, timeout=_BUNDLE_TIMEOUT_SECONDS, allow_redirects=False)
        response.raise_for_status()
        visited.add(script_url)
        total_bytes += len(response.content)
        assert total_bytes <= _MAX_BUNDLE_BYTES, "Angular bundle dependency graph exceeds the byte limit"
        javascript.append(response.text)
        for dependency in re.findall(r'["\']([^"\']+\.js)["\']', response.text):
            dependency_url = urljoin(script_url, dependency)
            assert _origin(dependency_url) == _origin(frontend_url), (
                "Angular JavaScript dependencies must stay on the Compose frontend"
            )
            if dependency_url not in visited:
                pending.append(dependency_url)
    return "\n".join(javascript)


@dataclass(frozen=True)
class _ComposeVoiceRun:
    run_id: str
    profile_id: str
    session_id: str
    candidate_ids: tuple[str, str]

    @classmethod
    def create(cls) -> _ComposeVoiceRun:
        run_id = uuid.uuid4().hex
        return cls(
            run_id=run_id,
            profile_id=f"angular-compose-profile-{run_id}",
            session_id=f"angular-compose-session-{run_id}",
            candidate_ids=(f"angular-compose-candidate-a-{run_id}", f"angular-compose-candidate-b-{run_id}"),
        )

    def headers(self, operation: str, *, mutation: bool = False) -> dict[str, str]:
        headers = {"X-Request-ID": f"angular-compose:{self.run_id}:{operation}"}
        if mutation:
            headers["Idempotency-Key"] = f"angular-compose:{self.run_id}:{operation}"
        return headers


class _HubVoiceApi:
    """Small test adapter that refuses every non-Hub voice lifecycle path."""

    def __init__(self, hub_url: str, token: str, run: _ComposeVoiceRun) -> None:
        self._hub_url = hub_url.rstrip("/")
        self._run = run
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def close(self) -> None:
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        mutation: bool = False,
        expected_status: int = 200,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parsed = urlsplit(path)
        assert not parsed.scheme and not parsed.netloc
        assert parsed.path.startswith("/v1/voice/"), f"lifecycle request bypasses the Hub voice API: {path}"
        response = self._session.request(
            method,
            f"{self._hub_url}{path}",
            headers=self._run.headers(operation, mutation=mutation),
            json=json,
            timeout=_HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        assert response.status_code == expected_status, (
            f"{method} {path} returned {response.status_code}, expected {expected_status}: {response.text[:1000]}"
        )
        payload = response.json()
        assert isinstance(payload, dict)
        assert payload.get("status") == ("error" if expected_status >= 400 else "success")
        data = payload.get("data")
        assert isinstance(data, dict)
        return data


@dataclass(frozen=True)
class _ComposeAuth:
    token: str
    provisioned_username: str | None = None


def _login(hub_url: str, *, run_id: str) -> _ComposeAuth:
    configured_username = str(os.getenv("INITIAL_ADMIN_USER", "admin"))
    configured_password = _required("INITIAL_ADMIN_PASSWORD")
    login = requests.post(
        f"{hub_url}/login",
        json={
            "username": configured_username,
            "password": configured_password,
        },
        timeout=_HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    if login.status_code == 401 and os.getenv("ANANTA_ALLOW_TEST_USER_PROVISIONING") == "1":
        username = f"voice-compose-{run_id}"
        password = f"Ananta-Voice-{uuid.uuid4().hex}!9a"
        provisioned = requests.post(
            f"{hub_url}/test/provision-user",
            json={
                "username": username,
                "password": password,
                "role": "admin",
                "overwrite": False,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        provisioned.raise_for_status()
        login = requests.post(
            f"{hub_url}/login",
            json={"username": username, "password": password},
            timeout=_HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        login.raise_for_status()
        token = str((login.json().get("data") or {}).get("access_token") or "")
        assert token, "Hub test-user login did not return an access token"
        return _ComposeAuth(token=token, provisioned_username=username)
    login.raise_for_status()
    token = str((login.json().get("data") or {}).get("access_token") or "")
    assert token, "Hub login did not return an access token"
    return _ComposeAuth(token=token)


def _delete_provisioned_user(hub_url: str, username: str | None) -> None:
    if not username:
        return
    response = requests.delete(
        f"{hub_url}/test/users/{username}",
        timeout=_HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    assert response.status_code in {200, 404}


def _create_real_compose_result_artifact(
    hub_url: str,
    token: str,
    run: _ComposeVoiceRun,
) -> str:
    response = requests.post(
        f"{hub_url}/test/voice-result-artifact",
        headers={
            "Authorization": f"Bearer {token}",
            **run.headers("result-fixture", mutation=True),
        },
        json={
            "profile_id": run.profile_id,
            "candidate_ids": list(run.candidate_ids),
        },
        timeout=_HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    assert response.status_code == 201, response.text[:1000]
    data = (response.json().get("data") or {})
    assert data.get("candidate_ids") == list(run.candidate_ids)
    result_ref = str(data.get("result_ref") or "")
    assert result_ref.startswith("voice-result-")
    return result_ref


def _assert_bundle_contract(javascript: str) -> None:
    # Dev-server and production bundles may encode non-ASCII template text
    # differently. The stable test id proves that the actual Voice settings
    # surface (not merely its route shell) was shipped.
    assert "voice-configuration" in javascript
    for path in _EXPECTED_HUB_BUNDLE_PATHS:
        assert path in javascript, f"Angular bundle is missing its Hub API path {path}"
    assert _SHARED_INVALID_COMBINATION_REASON in javascript
    for target in _FORBIDDEN_RUNTIME_BUNDLE_TARGETS:
        assert target not in javascript, f"Angular bundle exposes forbidden runtime target {target}"


def test_running_compose_serves_angular_and_completes_hub_voice_lifecycle() -> None:
    if _required("ANANTA_RUN_VOICE_ANGULAR_COMPOSE") != "1":
        pytest.skip("Angular Compose smoke requires ANANTA_RUN_VOICE_ANGULAR_COMPOSE=1")
    frontend_url = str(os.getenv("ANANTA_ANGULAR_COMPOSE_URL", "http://127.0.0.1:4200")).rstrip("/")
    hub_url = str(os.getenv("ANANTA_HUB_COMPOSE_URL", "http://127.0.0.1:5000")).rstrip("/")

    _assert_bundle_contract(_angular_bundle(frontend_url))
    run = _ComposeVoiceRun.create()
    authentication = _login(hub_url, run_id=run.run_id)
    api = _HubVoiceApi(hub_url, authentication.token, run)
    review_id = ""
    lifecycle_completed = False
    try:
        capabilities = api.request("GET", "/v1/voice/capabilities", operation="capabilities")
        assert isinstance(capabilities["available"], bool)
        assert capabilities["provider"] == "voice-runtime"
        assert "transcription" in capabilities["capabilities"]
        if capabilities["available"]:
            assert capabilities["health"]["ok"] is True
        else:
            assert capabilities["health"]["ok"] is False
            assert capabilities["health"]["status"] == "unavailable"
        assert capabilities["routing_details"]["owner"] == "hub"
        assert capabilities["routing_details"]["runtime_direct_client_access"] is False

        schema = api.request("GET", "/v1/voice/configuration/schema", operation="configuration-schema")["schema"]
        assert schema["schema_version"] == "ananta.voice-configuration.v1"
        assert schema["precedence"][-2:] == ["profile_delta", "session_delta"]

        consent = api.request(
            "PUT",
            f"/v1/voice/consents/{run.profile_id}",
            operation="consent-opt-in",
            mutation=True,
            json={
                "granted": True,
                "categories": ["preferences", "text_corrections", "vocabulary"],
                "retention_days": 30,
            },
        )["consent"]
        assert consent["granted"] is True
        assert consent["profile_id"] == run.profile_id
        assert set(consent["categories"]) == {"preferences", "text_corrections", "vocabulary"}
        confirmed_consent = api.request(
            "GET",
            f"/v1/voice/consents/{run.profile_id}",
            operation="consent-read",
        )["consent"]
        assert confirmed_consent["granted"] is True
        assert confirmed_consent["version"] == consent["version"]

        result_ref = _create_real_compose_result_artifact(
            hub_url,
            authentication.token,
            run,
        )

        review = api.request(
            "POST",
            "/v1/voice/reviews",
            operation="review-create",
            mutation=True,
            expected_status=201,
            json={
                "profile_id": run.profile_id,
                "session_id": run.session_id,
                "result_ref": result_ref,
                "candidate_ids": list(run.candidate_ids),
            },
        )["review"]
        review_id = review["id"]
        assert review["state"] == "pending"
        assert review["candidate_ids"] == list(run.candidate_ids)
        fetched_review = api.request(
            "GET",
            f"/v1/voice/reviews/{review_id}",
            operation="review-read-pending",
        )["review"]
        assert fetched_review["version"] == review["version"]

        profile_delta = api.request(
            "PUT",
            "/v1/voice/configuration",
            operation="configuration-profile",
            mutation=True,
            json={
                "scope": "profile",
                "scope_id": run.profile_id,
                "delta": {"confidence_threshold": 0.83, "review_policy": "on_disagreement"},
            },
        )["configuration"]
        assert profile_delta["scope"] == "profile"
        session_delta = api.request(
            "PUT",
            "/v1/voice/configuration",
            operation="configuration-session",
            mutation=True,
            json={
                "scope": "session",
                "scope_id": run.session_id,
                "delta": {"review_policy": "always"},
            },
        )["configuration"]
        assert session_delta["scope"] == "session"

        profile_configuration = api.request(
            "GET",
            f"/v1/voice/configuration?profile_id={run.profile_id}",
            operation="configuration-profile-read",
        )["configuration"]
        effective_configuration = api.request(
            "GET",
            f"/v1/voice/configuration?profile_id={run.profile_id}&session_id={run.session_id}",
            operation="configuration-session-read",
        )["configuration"]
        assert profile_configuration["effective"]["confidence_threshold"] == pytest.approx(0.83)
        assert profile_configuration["effective"]["review_policy"] == "on_disagreement"
        assert effective_configuration["effective"]["confidence_threshold"] == pytest.approx(0.83)
        assert effective_configuration["effective"]["review_policy"] == "always"
        assert [source["scope"] for source in effective_configuration["sources"]][-2:] == ["profile", "session"]
        assert [source["scope_id"] for source in effective_configuration["sources"]][-2:] == [
            run.profile_id,
            run.session_id,
        ]

        invalid = api.request(
            "PUT",
            "/v1/voice/configuration",
            operation="configuration-invalid-combination",
            mutation=True,
            expected_status=422,
            json={
                "scope": "session",
                "scope_id": run.session_id,
                "delta": {"primary_backend": "vosk", "secondary_backends": ["vosk"]},
            },
        )
        assert invalid["error"]["code"] == _SHARED_INVALID_COMBINATION_REASON

        decided = api.request(
            "POST",
            f"/v1/voice/reviews/{review_id}/decision",
            operation="review-decision",
            mutation=True,
            json={
                "decision": "accept",
                "expected_version": review["version"],
                "selected_candidate_id": run.candidate_ids[0],
            },
        )["review"]
        assert decided["state"] == "accepted"
        assert decided["selected_candidate_id"] == run.candidate_ids[0]
        assert decided["version"] == review["version"] + 1
        assert str(decided["decision_artifact_ref"]).startswith("voice-result-")
        lifecycle_completed = True
    finally:
        try:
            deletion = api.request(
                "DELETE",
                f"/v1/voice/privacy/{run.profile_id}",
                operation="privacy-delete",
                mutation=True,
                json={"confirmed": True},
            )["deletion"]
            assert deletion["profile_id"] == run.profile_id
            assert deletion["snapshots_revoked"] is True
            assert deletion["runtime_cleanup_pending"] is False
            if lifecycle_completed:
                assert deletion["deleted_count"] > 0
                assert deletion["deleted_by_store"]["voice_configuration_deltas"] == 2
            consent_after_delete = api.request(
                "GET",
                f"/v1/voice/consents/{run.profile_id}",
                operation="consent-read-after-delete",
            )["consent"]
            assert consent_after_delete["granted"] is False
            if review_id:
                missing_review = api.request(
                    "GET",
                    f"/v1/voice/reviews/{review_id}",
                    operation="review-read-after-delete",
                    expected_status=404,
                )
                assert missing_review["error"]["code"] == "voice_review.not_found"
            cleaned_configuration = api.request(
                "GET",
                f"/v1/voice/configuration?profile_id={run.profile_id}&session_id={run.session_id}",
                operation="configuration-read-after-delete",
            )["configuration"]
            assert not {"profile", "session"}.intersection(
                source["scope"] for source in cleaned_configuration["sources"]
            )
        finally:
            api.close()
            _delete_provisioned_user(hub_url, authentication.provisioned_username)

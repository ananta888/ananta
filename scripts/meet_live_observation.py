"""Bounded, read-only public observations; never activate trust or expose credentials."""

import json
import re
import subprocess
from urllib.parse import urlsplit

from agent.services.meet_contract import MeetProfile
from scripts.meet_integration_observation import integration_projection

MAX_BODY = 16384


def _closed_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("meet_readiness_duplicate_json")
        value[key] = item
    return value


def _json_command(command, timeout, *, execute=subprocess.run):
    try:
        result = execute(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=True)
        if len(result.stdout) > MAX_BODY:
            return None
        value = json.loads(result.stdout, object_pairs_hook=_closed_duplicates)
        return value if isinstance(value, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        return None  # No raw stdout/stderr, URLs, credentials or exception text.


def public_observation(origin, path, *, local_tls_route=False, execute=subprocess.run):
    MeetProfile(origin)
    if (
        path not in ("/healthz", "/config", "/api/machine/capabilities", "/api/machine/integration")
        or type(local_tls_route) is not bool
    ):
        raise ValueError("meet_readiness_request_invalid")
    command = [
        "curl",
        "--disable",  # Must be the first option: ignore user curlrc, including auth/redirect settings.
        "--fail",
        "--silent",
        "--noproxy",
        "*",
        "--globoff",
        "--proto",
        "=https",
        "--max-redirs",
        "0",
        "--connect-timeout",
        "5",
        "--max-time",
        "10",
        "--max-filesize",
        str(MAX_BODY),
    ]
    if local_tls_route:
        command += ["--resolve", f"{urlsplit(origin).hostname}:443:127.0.0.1"]
    command.append(origin + path)
    return _json_command(command, 12, execute=execute)


def container_observation(name, *, execute=subprocess.run):
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ValueError("meet_readiness_container_invalid")
    # Only these three Docker fields leave the daemon. Never request Config.Env.
    template = (
        '{"image_id":{{json .Image}},"revision":'
        '{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
        '"running":{{json .State.Running}}}'
    )
    value = _json_command(["docker", "inspect", "--type=container", "--format", template, name], 5, execute=execute)
    return container_projection(value)


def container_projection(value):
    if not isinstance(value, dict):
        return None
    image, revision = value.get("image_id"), value.get("revision")
    return {
        "image_id": image if isinstance(image, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", image) else None,
        "revision": revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision) else None,
        "running": value.get("running") is True,
    }


def readiness_report(origin, health, config, capabilities, container, *, local_tls_route=False, integration=None):
    MeetProfile(origin)
    health, config, capabilities = (
        value if isinstance(value, dict) else {} for value in (health, config, capabilities)
    )
    auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    encryption = config.get("mediaE2ee") if isinstance(config.get("mediaE2ee"), dict) else {}
    checks = {
        "https_health": health.get("status") == "ok",
        "human_auth_required": auth.get("mode") == "required",
        "sframe_required": encryption.get("mode") == "required",
        "machine_admission_enabled": capabilities.get("schema") == "ananta.meet-capabilities.v1"
        and capabilities.get("admissionEnabled") is True,
        "turn_configured": config.get("turnConfigured") is True,
    }
    counts = {
        key: health.get(key) if type(health.get(key)) is int and 0 <= health[key] <= 1_000_000 else None
        for key in ("rooms", "participants")
    }
    return {
        "schema": "ananta.meet-live-readiness.v1",
        "origin": origin,
        "route": "local-tls-hairpin" if local_tls_route else "host-dns-https",
        "status": "observed" if all(checks.values()) else "blocked",
        "checks": checks,
        "occupancy_snapshot": counts,
        "container": container_projection(container),
        "integration": integration_projection(integration, capabilities),
        "unverified": [
            "container_is_public_upstream",
            "exact_hub_trust_scope_and_key",
            "current_hub_project_preauthorization",
            "selected_turn_pair_and_payload",
            "independent_external_receiver",
            "public_dialog_and_soak",
        ],
        "production_release_eligible": False,
    }

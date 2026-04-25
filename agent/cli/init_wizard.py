from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from agent.cli.deployment_profile_writer import (
    build_deployment_profile,
    write_deployment_profile,
)
from agent.local_llm_backends import normalize_openai_compatible_base_url
from agent.runtime_profiles import runtime_profile_catalog
from agent.services.runtime_profile_recommender import (
    EnvironmentKind,
    RuntimeRecommendation,
    RuntimeRecommendationRequest,
    recommend_runtime_profile,
)

RUNTIME_MODES = ("local-dev", "sandbox", "strict")
LLM_BACKENDS = ("ollama", "lmstudio", "openai-compatible", "manual")

_RUNTIME_MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "local-dev": {
        "runtime_profile": "local-dev",
        "governance_mode": "safe",
        "platform_mode": "local-dev",
        "container_required": False,
        "container_recommendation": "none",
    },
    "sandbox": {
        "runtime_profile": "compose-safe",
        "governance_mode": "balanced",
        "platform_mode": "trusted-internal",
        "container_required": True,
        "container_recommendation": "optional",
    },
    "strict": {
        "runtime_profile": "distributed-strict",
        "governance_mode": "strict",
        "platform_mode": "admin-only",
        "container_required": True,
        "container_recommendation": "isolated",
    },
}

_DEFAULT_ENDPOINTS = {
    "ollama": "http://localhost:11434/api/generate",
    "lmstudio": "http://localhost:1234/v1",
    "openai-compatible": "http://localhost:1234/v1",
}

_DEFAULT_MODELS = {
    "ollama": "ananta-default",
    "lmstudio": "model",
    "openai-compatible": "model",
}


@dataclass(frozen=True)
class InitAnswers:
    runtime_mode: str
    runtime_mode_source: str
    hardware_profile: EnvironmentKind
    llm_backend: str
    endpoint_url: str | None
    model: str | None
    api_key_env: str | None
    manual_backend_config: dict[str, Any] | None


def detect_runtime_mode(
    *,
    env: Mapping[str, str] | None = None,
    docker_env_exists: bool | None = None,
) -> tuple[str, str]:
    values = env or os.environ
    explicit = str(values.get("ANANTA_RUNTIME_MODE") or "").strip().lower()
    if explicit in RUNTIME_MODES:
        return explicit, "env.ANANTA_RUNTIME_MODE"

    compose_profiles = str(values.get("COMPOSE_PROFILES") or "").strip().lower()
    if "distributed" in compose_profiles or "strict" in compose_profiles:
        return "strict", "env.COMPOSE_PROFILES"
    if compose_profiles:
        return "sandbox", "env.COMPOSE_PROFILES"

    in_container = Path("/.dockerenv").exists() if docker_env_exists is None else bool(docker_env_exists)
    if in_container or bool(values.get("KUBERNETES_SERVICE_HOST")):
        return "sandbox", "heuristic.container_runtime"

    return "local-dev", "heuristic.local_default"


def _normalize_ollama_url(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return _DEFAULT_ENDPOINTS["ollama"]
    parsed = urlparse(value)
    if parsed.path.strip("/") == "":
        value = value.rstrip("/") + "/api/generate"
    return value


def _prompt_choice(
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    prompt: str,
    choices: tuple[str, ...],
    default: str,
) -> str:
    accepted = {choice.lower(): choice for choice in choices}
    while True:
        raw = str(input_fn(f"{prompt} [{default}]: ")).strip().lower()
        if not raw:
            return default
        if raw in accepted:
            return accepted[raw]
        output_fn(f"Invalid value. Allowed: {', '.join(choices)}")


def _prompt_value(
    *,
    input_fn: Callable[[str], str],
    prompt: str,
    default: str,
) -> str:
    raw = str(input_fn(f"{prompt} [{default}]: ")).strip()
    return raw or default


def _parse_manual_backend(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid manual backend JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("manual backend JSON must be an object")
    return parsed


def _resolve_runtime_mode(
    *,
    requested_mode: str,
    detected_mode: str,
    interactive: bool,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str:
    requested = str(requested_mode or "auto").strip().lower()
    if requested != "auto":
        if requested not in RUNTIME_MODES:
            raise ValueError(f"invalid runtime mode '{requested_mode}'")
        return requested
    if interactive:
        output_fn(f"Detected runtime mode: {detected_mode}")
        return _prompt_choice(
            input_fn=input_fn,
            output_fn=output_fn,
            prompt="Select runtime mode (local-dev, sandbox, strict)",
            choices=RUNTIME_MODES,
            default=detected_mode,
        )
    return detected_mode


def _resolve_llm_backend(
    *,
    requested_backend: str | None,
    interactive: bool,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str:
    if requested_backend:
        backend = str(requested_backend).strip().lower()
        if backend not in LLM_BACKENDS:
            raise ValueError(f"invalid llm backend '{requested_backend}'")
        return backend
    if interactive:
        return _prompt_choice(
            input_fn=input_fn,
            output_fn=output_fn,
            prompt="Select LLM backend (ollama, lmstudio, openai-compatible, manual)",
            choices=LLM_BACKENDS,
            default="ollama",
        )
    return "ollama"


def collect_answers(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    env: Mapping[str, str] | None = None,
    docker_env_exists: bool | None = None,
) -> InitAnswers:
    interactive = not bool(args.yes)
    detected_mode, mode_source = detect_runtime_mode(env=env, docker_env_exists=docker_env_exists)
    runtime_mode = _resolve_runtime_mode(
        requested_mode=args.runtime_mode,
        detected_mode=detected_mode,
        interactive=interactive,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    llm_backend = _resolve_llm_backend(
        requested_backend=args.llm_backend,
        interactive=interactive,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    hardware_profile_raw = str(args.hardware_profile or "cpu-only").strip().lower()
    allowed_hardware_profiles = {"cpu-only", "nvidia-gpu", "remote-model", "mixed-local-remote"}
    if hardware_profile_raw not in allowed_hardware_profiles:
        raise ValueError(f"invalid hardware profile '{args.hardware_profile}'")
    hardware_profile = _normalize_hardware_profile(hardware_profile_raw)

    endpoint_url: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    manual_backend_config: dict[str, Any] | None = None

    if llm_backend in {"ollama", "lmstudio", "openai-compatible"}:
        default_endpoint = _DEFAULT_ENDPOINTS[llm_backend]
        default_model = _DEFAULT_MODELS[llm_backend]
        endpoint_url = str(args.endpoint_url or "").strip()
        model = str(args.model or "").strip()
        if not endpoint_url:
            if interactive:
                endpoint_url = _prompt_value(
                    input_fn=input_fn,
                    prompt=f"{llm_backend} endpoint URL",
                    default=default_endpoint,
                )
            else:
                endpoint_url = default_endpoint
        if not model:
            if interactive:
                model = _prompt_value(
                    input_fn=input_fn,
                    prompt=f"{llm_backend} default model",
                    default=default_model,
                )
            else:
                model = default_model
        if llm_backend == "openai-compatible":
            api_key_env = str(args.api_key_env or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY"
    else:
        raw_manual_json = str(args.manual_json or "").strip()
        if not raw_manual_json:
            if interactive:
                raw_manual_json = _prompt_value(
                    input_fn=input_fn,
                    prompt="manual backend JSON object",
                    default='{"default_provider":"custom","default_model":"model"}',
                )
            else:
                raise ValueError("manual backend requires --manual-json when --yes is used")
        manual_backend_config = _parse_manual_backend(raw_manual_json)

    if llm_backend == "ollama":
        endpoint_url = _normalize_ollama_url(endpoint_url)
    elif llm_backend in {"lmstudio", "openai-compatible"}:
        endpoint_url = normalize_openai_compatible_base_url(endpoint_url) or _DEFAULT_ENDPOINTS[llm_backend]

    return InitAnswers(
        runtime_mode=runtime_mode,
        runtime_mode_source=mode_source if str(args.runtime_mode or "auto").strip().lower() == "auto" else "cli.argument",
        hardware_profile=hardware_profile,
        llm_backend=llm_backend,
        endpoint_url=endpoint_url,
        model=model,
        api_key_env=api_key_env,
        manual_backend_config=manual_backend_config,
    )


def _build_backend_config_patch(answers: InitAnswers) -> dict[str, Any]:
    if answers.llm_backend == "ollama":
        return {
            "default_provider": "ollama",
            "default_model": answers.model or _DEFAULT_MODELS["ollama"],
            "ollama_url": answers.endpoint_url or _DEFAULT_ENDPOINTS["ollama"],
        }
    if answers.llm_backend == "lmstudio":
        return {
            "default_provider": "lmstudio",
            "default_model": answers.model or _DEFAULT_MODELS["lmstudio"],
            "lmstudio_url": answers.endpoint_url or _DEFAULT_ENDPOINTS["lmstudio"],
        }
    if answers.llm_backend == "openai-compatible":
        backend_id = "local-openai"
        return {
            "default_provider": backend_id,
            "default_model": answers.model or _DEFAULT_MODELS["openai-compatible"],
            "local_openai_backends": [
                {
                    "id": backend_id,
                    "name": "Local OpenAI Compatible",
                    "base_url": answers.endpoint_url or _DEFAULT_ENDPOINTS["openai-compatible"],
                    "models": [answers.model or _DEFAULT_MODELS["openai-compatible"]],
                    "supports_tool_calls": True,
                    "api_key_profile": answers.api_key_env or "OPENAI_API_KEY",
                }
            ],
        }
    if answers.manual_backend_config is None:
        raise ValueError("manual backend config missing")
    return dict(answers.manual_backend_config)


def build_runtime_profile_document(
    answers: InitAnswers,
    *,
    now: datetime,
) -> dict[str, Any]:
    defaults = dict(_RUNTIME_MODE_DEFAULTS[answers.runtime_mode])
    runtime_profile_name = str(defaults["runtime_profile"])
    if runtime_profile_name not in runtime_profile_catalog():
        raise ValueError(f"runtime profile '{runtime_profile_name}' is not present in the catalog")

    base_patch = {
        "runtime_profile": runtime_profile_name,
        "governance_mode": str(defaults["governance_mode"]),
        "platform_mode": str(defaults["platform_mode"]),
    }
    recommendation = recommend_runtime_profile(
        RuntimeRecommendationRequest(
            environment=answers.hardware_profile,
            allow_paid_providers=False,
            explicit_remote_endpoint=answers.endpoint_url if answers.llm_backend == "openai-compatible" else None,
        )
    )
    backend_patch = _build_backend_config_patch(answers)
    config_patch = _deep_merge(base_patch, backend_patch)

    backend_payload: dict[str, Any] = {
        "kind": answers.llm_backend,
        "endpoint_url": answers.endpoint_url,
        "model": answers.model,
        "api_key_env": answers.api_key_env,
    }
    if answers.manual_backend_config is not None:
        backend_payload["manual_config"] = dict(answers.manual_backend_config)

    return {
        "schema": "ananta.runtime-profile.v1",
        "version": "1",
        "created_at": now.isoformat(),
        "runtime_mode": answers.runtime_mode,
        "runtime_mode_source": answers.runtime_mode_source,
        "hardware_profile": answers.hardware_profile,
        "runtime_profile": runtime_profile_name,
        "governance_mode": str(defaults["governance_mode"]),
        "platform_mode": str(defaults["platform_mode"]),
        "runtime_recommendation": _recommendation_payload(recommendation),
        "llm_backend": backend_payload,
        "container_runtime": {
            "required": bool(defaults["container_required"]),
            "recommendation": str(defaults["container_recommendation"]),
        },
        "config_patch": config_patch,
    }


def _recommendation_payload(recommendation: RuntimeRecommendation) -> dict[str, Any]:
    context_window_tokens = int(recommendation.context_window_tokens)
    if context_window_tokens <= 12000:
        window_profile = "compact_12k"
    elif context_window_tokens <= 32000:
        window_profile = "standard_32k"
    else:
        window_profile = "full_64k"
    return {
        "environment": recommendation.environment,
        "provider": recommendation.provider,
        "model": recommendation.model,
        "limits": {
            "context_window_tokens": recommendation.context_window_tokens,
            "max_input_tokens": recommendation.max_input_tokens,
            "max_output_tokens": recommendation.max_output_tokens,
            "rag_budget_tokens": recommendation.rag_budget_tokens,
            "patch_size_lines": recommendation.patch_size_lines,
            "window_profile": window_profile,
        },
        "execution_mix": {
            "local_weight": recommendation.local_execution_weight,
            "remote_weight": recommendation.remote_execution_weight,
        },
        "requires_explicit_provider_config": recommendation.requires_explicit_provider_config,
        "notes": list(recommendation.notes),
    }


def _normalize_hardware_profile(value: str) -> EnvironmentKind:
    if value == "cpu-only":
        return "cpu-only"
    if value == "nvidia-gpu":
        return "nvidia-gpu"
    if value == "remote-model":
        return "remote-model"
    if value == "mixed-local-remote":
        return "mixed-local-remote"
    raise ValueError(f"invalid hardware profile '{value}'")


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
            continue
        merged[key] = value
    return merged


def _write_json(path: Path, payload: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"target file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config file must be a JSON object: {path}")
    return raw


def apply_config_patch(
    *,
    config_path: Path,
    patch: dict[str, Any],
    force: bool,
) -> None:
    config_object = _load_json_object(config_path)
    merged = _deep_merge(config_object, patch)
    _write_json(config_path, merged, force=force or config_path.exists())


def run_init(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    docker_env_exists: bool | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, str | None]:
    base_dir = Path(cwd) if cwd else Path.cwd()
    answers = collect_answers(
        args,
        input_fn=input_fn,
        output_fn=output_fn,
        env=env,
        docker_env_exists=docker_env_exists,
    )
    now = now_fn() if now_fn else datetime.now(timezone.utc)
    profile_document = build_runtime_profile_document(answers, now=now)

    profile_path = (base_dir / str(args.profile_path)).resolve()
    _write_json(profile_path, profile_document, force=bool(args.force))

    config_path: Path | None = None
    if args.apply_config:
        config_path = (base_dir / str(args.config_path)).resolve()
        apply_config_patch(
            config_path=config_path,
            patch=dict(profile_document.get("config_patch") or {}),
            force=bool(args.force),
        )

    deployment_profile_path: Path | None = None
    deployment_backup_path: str | None = None
    deployment_target = str(args.deployment_target or "none").strip().lower()
    if deployment_target != "none":
        deployment_profile_path = (base_dir / str(args.deployment_path)).resolve()
        deployment_payload = build_deployment_profile(
            runtime_mode=answers.runtime_mode,
            runtime_profile=str(profile_document["runtime_profile"]),
            governance_mode=str(profile_document["governance_mode"]),
            target=deployment_target,
            config_patch=dict(profile_document.get("config_patch") or {}),
        )
        write_result = write_deployment_profile(
            path=deployment_profile_path,
            payload=deployment_payload,
            overwrite_confirmed=bool(args.force),
            backup_existing=bool(args.backup_existing_deployment),
        )
        deployment_backup_path = write_result.backup_path

    output_fn("Ananta init completed.")
    output_fn(f"Runtime mode: {profile_document['runtime_mode']} (source: {profile_document['runtime_mode_source']})")
    output_fn(f"Runtime profile file: {profile_path}")
    output_fn(f"Hardware profile: {answers.hardware_profile}")
    if config_path is not None:
        output_fn(f"Config patch applied: {config_path}")
    if deployment_profile_path is not None:
        output_fn(f"Deployment profile file: {deployment_profile_path}")
        if deployment_backup_path:
            output_fn(f"Deployment backup file: {deployment_backup_path}")
    output_fn(f"Selected backend: {answers.llm_backend}")
    return {
        "profile_path": str(profile_path),
        "config_path": str(config_path) if config_path else None,
        "deployment_profile_path": str(deployment_profile_path) if deployment_profile_path else None,
        "deployment_backup_path": deployment_backup_path,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ananta init",
        description="Initialize a reviewable runtime profile for Ananta.",
    )
    parser.add_argument(
        "--runtime-mode",
        default="auto",
        choices=("auto", *RUNTIME_MODES),
        help="Runtime mode to configure. auto detects local-dev/sandbox/strict.",
    )
    parser.add_argument(
        "--llm-backend",
        default=None,
        choices=LLM_BACKENDS,
        help="Preferred LLM backend.",
    )
    parser.add_argument(
        "--hardware-profile",
        default="cpu-only",
        choices=("cpu-only", "nvidia-gpu", "remote-model", "mixed-local-remote"),
        help="Hardware/runtime topology for conservative recommendation defaults.",
    )
    parser.add_argument("--endpoint-url", default="", help="Backend endpoint URL (optional).")
    parser.add_argument("--model", default="", help="Default model identifier (optional).")
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="API key environment variable name for openai-compatible backend.",
    )
    parser.add_argument(
        "--manual-json",
        default="",
        help="Manual backend JSON object when --llm-backend manual is used.",
    )
    parser.add_argument(
        "--profile-path",
        default="ananta.runtime-profile.json",
        help="Path for the generated runtime profile file.",
    )
    parser.add_argument("--apply-config", action="store_true", help="Apply generated config patch to config.json.")
    parser.add_argument("--config-path", default="config.json", help="Config path used with --apply-config.")
    parser.add_argument(
        "--deployment-target",
        default="none",
        choices=("none", "docker-compose", "podman"),
        help="Optionally generate deployment profile examples.",
    )
    parser.add_argument(
        "--deployment-path",
        default="ananta.deployment-profile.json",
        help="Path for generated deployment profile file.",
    )
    parser.add_argument(
        "--backup-existing-deployment",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Backup existing deployment profile before overwrite if --force is not set.",
    )
    parser.add_argument("--yes", action="store_true", help="Use defaults for missing values and skip prompts.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_init(args)
    except (ValueError, FileExistsError) as exc:
        print(f"Error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

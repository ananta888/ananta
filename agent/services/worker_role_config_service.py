"""WorkerRoleConfig-v1: Konfiguration und Validierung der Worker-Rollen
für das Heuristic-Runtime-System.

Policy-Referenz: docs/security/heuristic-runtime-policy.md
Schema:          schemas/worker/worker_role_config.v1.json
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_FORBIDDEN_CONTROL_WORKERS = frozenset({"opencode", "open_code"})

_DOMAIN_TTL_DEFAULTS: dict[str, dict[str, float]] = {
    "snake_tui":       {"min": 1.0, "max": 60.0, "default": 7.0},
    "snake_eclipse":   {"min": 1.0, "max": 60.0, "default": 7.0},
    "chat_codecompass":{"min": 1.0, "max": 60.0, "default": 15.0},
}


class WorkerRoleConfigError(ValueError):
    pass


@dataclass
class WorkerRoleTtlConfig:
    ttl_min_seconds: float = 1.0
    ttl_max_seconds: float = 60.0
    ttl_default_seconds: float = 7.0


@dataclass
class WorkerRoleConfig:
    runtime_mode: str = "local"
    control_worker: str = "ananta-worker"
    evolution_worker: str = "ananta-worker"
    code_implementation_worker: str = "opencode"
    auto_activation: bool = False
    domain_ttl: dict[str, WorkerRoleTtlConfig] = field(default_factory=dict)

    def ttl_for(self, domain: str) -> WorkerRoleTtlConfig:
        if domain in self.domain_ttl:
            return self.domain_ttl[domain]
        d = _DOMAIN_TTL_DEFAULTS.get(domain, {"min": 1.0, "max": 60.0, "default": 7.0})
        return WorkerRoleTtlConfig(
            ttl_min_seconds=d["min"],
            ttl_max_seconds=d["max"],
            ttl_default_seconds=d["default"],
        )


class WorkerRoleConfigService:
    def normalize(self, raw: dict[str, Any] | None) -> WorkerRoleConfig:
        cfg = dict(raw or {})

        runtime_mode = str(cfg.get("runtime_mode") or "local").strip().lower()
        if runtime_mode not in {"local", "hybrid"}:
            runtime_mode = "local"

        control_worker = str(cfg.get("control_worker") or "ananta-worker").strip().lower()
        evolution_worker = str(cfg.get("evolution_worker") or "ananta-worker").strip().lower()
        code_impl_worker = str(cfg.get("code_implementation_worker") or "opencode").strip().lower()
        auto_activation = bool(cfg.get("auto_activation", False))

        domain_ttl: dict[str, WorkerRoleTtlConfig] = {}
        overrides = cfg.get("domain_overrides") or {}
        if isinstance(overrides, dict):
            for domain, ov in overrides.items():
                if not isinstance(ov, dict):
                    continue
                defaults = _DOMAIN_TTL_DEFAULTS.get(domain, {"min": 1.0, "max": 60.0, "default": 7.0})
                domain_ttl[domain] = WorkerRoleTtlConfig(
                    ttl_min_seconds=float(ov.get("ttl_min_seconds") or defaults["min"]),
                    ttl_max_seconds=float(ov.get("ttl_max_seconds") or defaults["max"]),
                    ttl_default_seconds=float(ov.get("ttl_default_seconds") or defaults["default"]),
                )

        return WorkerRoleConfig(
            runtime_mode=runtime_mode,
            control_worker=control_worker,
            evolution_worker=evolution_worker,
            code_implementation_worker=code_impl_worker,
            auto_activation=auto_activation,
            domain_ttl=domain_ttl,
        )

    def validate(self, cfg: WorkerRoleConfig) -> list[str]:
        errors: list[str] = []

        if cfg.control_worker in _FORBIDDEN_CONTROL_WORKERS:
            errors.append(f"opencode_not_allowed_as_heuristic_controller:control_worker={cfg.control_worker}")
        if cfg.evolution_worker in _FORBIDDEN_CONTROL_WORKERS:
            errors.append(f"opencode_not_allowed_as_heuristic_controller:evolution_worker={cfg.evolution_worker}")
        if cfg.auto_activation:
            errors.append("auto_activation_must_be_false")

        for domain, ttl in cfg.domain_ttl.items():
            if ttl.ttl_min_seconds < 1:
                errors.append(f"ttl_out_of_range:{domain}:min<1")
            if ttl.ttl_max_seconds > 60:
                errors.append(f"ttl_out_of_range:{domain}:max>60")
            if ttl.ttl_default_seconds < ttl.ttl_min_seconds:
                errors.append(f"ttl_out_of_range:{domain}:default<min")
            if ttl.ttl_default_seconds > ttl.ttl_max_seconds:
                errors.append(f"ttl_out_of_range:{domain}:default>max")

        return errors

    def normalize_and_validate(self, raw: dict[str, Any] | None) -> WorkerRoleConfig:
        cfg = self.normalize(raw)
        errors = self.validate(cfg)
        if errors:
            raise WorkerRoleConfigError("; ".join(errors))
        return cfg


_DEFAULT_CONFIG = WorkerRoleConfig()
_SERVICE = WorkerRoleConfigService()


def get_worker_role_config_service() -> WorkerRoleConfigService:
    return _SERVICE


def get_default_worker_role_config() -> WorkerRoleConfig:
    return _DEFAULT_CONFIG

from __future__ import annotations


def build_docker_health_command(service_name: str) -> str:
    return f"docker compose ps {service_name}"


def is_service_healthy(output: str) -> bool:
    return "healthy" in output.lower()

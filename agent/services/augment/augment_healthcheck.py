from __future__ import annotations
import shutil, subprocess, time
from dataclasses import dataclass, field
from typing import Any

@dataclass
class HealthCheckResult:
    check_name: str
    passed: bool
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class AugmentHealthStatus:
    overall: str    # "ok" | "degraded" | "unavailable" | "misconfigured"
    checks: list[HealthCheckResult]
    auggie_version: str | None
    node_version: str | None
    checked_at: float

    def is_ready_for_context_provider(self) -> bool:
        return self.overall in ("ok", "degraded") and any(
            c.check_name == "auggie_binary" and c.passed for c in self.checks
        )

    def is_ready_for_cli_worker(self) -> bool:
        return self.overall == "ok" and all(
            c.passed for c in self.checks if c.check_name in ("node", "auggie_binary")
        )

class AugmentHealthcheck:
    TIMEOUT = 5

    def run(self, *, check_mcp: bool = False, check_cli: bool = True) -> AugmentHealthStatus:
        checks = []
        checks.append(self._check_node())
        checks.append(self._check_auggie_binary())
        if shutil.which("auggie"):
            checks.append(self._check_auggie_version())

        passed = [c for c in checks if c.passed]
        if not any(c.check_name == "auggie_binary" and c.passed for c in checks):
            overall = "unavailable"
        elif len(passed) == len(checks):
            overall = "ok"
        else:
            overall = "degraded"

        auggie_ver = next((c.metadata.get("version") for c in checks if c.check_name == "auggie_version" and c.passed), None)
        node_ver = next((c.metadata.get("version") for c in checks if c.check_name == "node" and c.passed), None)

        return AugmentHealthStatus(
            overall=overall, checks=checks,
            auggie_version=auggie_ver, node_version=node_ver,
            checked_at=time.time(),
        )

    def _check_node(self) -> HealthCheckResult:
        try:
            binary = shutil.which("node")
            if not binary:
                return HealthCheckResult("node", False, "node binary not found")
            result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=self.TIMEOUT)
            ver = result.stdout.strip()
            try:
                major = int(ver.lstrip("v").split(".")[0])
                if major >= 20:
                    return HealthCheckResult("node", True, f"node {ver}", {"version": ver})
                return HealthCheckResult("node", False, f"node {ver} < 20 required")
            except (ValueError, IndexError):
                return HealthCheckResult("node", False, f"Cannot parse version: {ver}")
        except Exception as e:
            return HealthCheckResult("node", False, f"Error: {e}")

    def _check_auggie_binary(self) -> HealthCheckResult:
        try:
            binary = shutil.which("auggie")
            if binary:
                return HealthCheckResult("auggie_binary", True, f"Found at {binary}", {"path": binary})
            return HealthCheckResult("auggie_binary", False, "auggie binary not found in PATH")
        except Exception as e:
            return HealthCheckResult("auggie_binary", False, f"Error: {e}")

    def _check_auggie_version(self) -> HealthCheckResult:
        try:
            result = subprocess.run(["auggie", "--version"], capture_output=True, text=True, timeout=self.TIMEOUT)
            ver = (result.stdout + result.stderr).strip()
            if ver:
                return HealthCheckResult("auggie_version", True, ver, {"version": ver})
            return HealthCheckResult("auggie_version", False, "No version output")
        except subprocess.TimeoutExpired:
            return HealthCheckResult("auggie_version", False, "Timeout getting version")
        except Exception as e:
            return HealthCheckResult("auggie_version", False, f"Error: {e}")

    def _check_noninteractive_mode(self) -> HealthCheckResult:
        try:
            result = subprocess.run(["auggie", "--help"], capture_output=True, text=True, timeout=self.TIMEOUT)
            output = result.stdout + result.stderr
            if "--print" in output or "--quiet" in output:
                return HealthCheckResult("noninteractive_mode", True, "--print --quiet supported")
            return HealthCheckResult("noninteractive_mode", False, "--print --quiet not found in help")
        except Exception as e:
            return HealthCheckResult("noninteractive_mode", False, f"Error: {e}")

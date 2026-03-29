import logging
import os
from typing import Optional

from agent.config import settings


def get_host_gateway_ip() -> Optional[str]:
    """Versucht die IP des Host-Gateways (WSL2/Docker) zu finden."""
    try:
        import subprocess

        # Unter Linux/Docker/WSL2 ist der Host oft das default gateway.
        ip_cmd = "/sbin/ip"
        if not os.path.exists(ip_cmd):
            ip_cmd = "/usr/sbin/ip"
        if not os.path.exists(ip_cmd):
            ip_cmd = "ip"
        output = subprocess.check_output(  # noqa: S603 - diagnostic read-only network query
            [ip_cmd, "route", "show", "default"], stderr=subprocess.DEVNULL
        )  # noqa: S607 - prefers absolute ip path, falls back when unavailable
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")

        for line in str(output).splitlines():
            line = line.strip()
            if not line:
                continue

            # Fallback: einige Aufrufer/Tests liefern nur die nackte Gateway-IP.
            if " " not in line and "." in line:
                return line

            parts = line.split()
            if "via" in parts:
                via_index = parts.index("via")
                if via_index + 1 < len(parts):
                    gateway = parts[via_index + 1].strip()
                    if gateway and "." in gateway:
                        return gateway
    except Exception:
        pass
    return None

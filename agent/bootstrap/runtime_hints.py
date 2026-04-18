import logging
import os

from agent.config import settings


def log_runtime_hints() -> None:
    """Logs actionable host/runtime hints for common local Docker issues."""
    if settings.redis_url:
        try:
            overcommit_path = "/proc/sys/vm/overcommit_memory"
            if os.path.exists(overcommit_path):
                with open(overcommit_path, "r", encoding="utf-8") as f:
                    overcommit = f.read().strip()
                if overcommit != "1":
                    msg = (
                        "Host kernel setting vm.overcommit_memory=%s detected. "
                        "Redis can become unstable under memory pressure. "
                        "Run setup_host_services.ps1 on Windows host."
                    )
                    # In containers this is usually controlled by the host/WSL runtime and
                    # cannot be changed from inside the app process.
                    if os.path.exists("/.dockerenv"):
                        logging.info(msg, overcommit)
                    else:
                        logging.warning(msg, overcommit)
        except Exception as e:
            logging.debug(f"Could not read vm.overcommit_memory: {e}")


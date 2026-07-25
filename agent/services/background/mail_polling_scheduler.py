from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from agent.services.mail_account_service import MailAccountService
from agent.services.mail_polling_scheduler import (
    MailPollingScheduler,
    MailPollingSchedulerConfig,
    PersistentMailPollingLease,
)
from agent.services.mail_runtime_policy import (
    get_mail_circuit_breaker,
    get_mail_health_registry,
    get_mail_runtime_policy,
)
from agent.services.mail_task_service import get_mail_task_service

_EXTENSION_KEY = "mail_polling_scheduler"


def start_mail_polling_scheduler(app: Any) -> None:
    from agent.config import settings

    extensions = getattr(app, "extensions", None)
    if settings.role != "hub" or not isinstance(extensions, dict):
        return
    existing = extensions.get(_EXTENSION_KEY)
    if isinstance(existing, Mapping):
        scheduler = existing.get("scheduler")
        thread = getattr(scheduler, "thread", None)
        if thread is not None and thread.is_alive():
            return
    config = MailPollingSchedulerConfig.from_environment(os.environ)
    repo_root = Path(os.environ.get("ANANTA_REPO_ROOT") or ".").resolve()
    scheduler = MailPollingScheduler(
        accounts=MailAccountService(
            store_path=repo_root / "data" / "mail" / "accounts-v2.json"
        ),
        tasks=get_mail_task_service(),
        runtime=get_mail_runtime_policy(),
        health=get_mail_health_registry(),
        lease=PersistentMailPollingLease(
            path=repo_root / "data" / "mail" / "polling-scheduler-v1.json"
        ),
        config=config,
        circuit_breaker=get_mail_circuit_breaker(),
    )
    scheduler.start()
    extensions[_EXTENSION_KEY] = {
        "enabled": config.enabled,
        "scheduler": scheduler,
    }
    thread = scheduler.thread
    if thread is not None:
        import agent.common.context

        agent.common.context.active_threads.append(thread)


def stop_mail_polling_scheduler(app: Any) -> None:
    extensions = getattr(app, "extensions", None)
    state = (
        extensions.get(_EXTENSION_KEY)
        if isinstance(extensions, dict)
        else None
    )
    if not isinstance(state, Mapping):
        return
    scheduler = state.get("scheduler")
    if isinstance(scheduler, MailPollingScheduler):
        scheduler.stop()


__all__ = [
    "start_mail_polling_scheduler",
    "stop_mail_polling_scheduler",
]

from __future__ import annotations

import json

from agent.db_models import ConfigDB
from agent.repository import config_repo


class TriggerRuntimeService:
    """Runtime facade around trigger engine configuration and webhook parsing."""

    def _engine(self):
        from agent.routes.tasks.triggers import trigger_engine

        return trigger_engine

    def status(self) -> dict:
        return self._engine().status()

    def configure(
        self,
        *,
        enabled_sources=None,
        webhook_secrets=None,
        auto_start_planner=None,
        ip_whitelists=None,
        rate_limits=None,
        persist_key: str | None = None,
    ) -> dict:
        new_config = self._engine().configure(
            enabled_sources=enabled_sources,
            webhook_secrets=webhook_secrets,
            auto_start_planner=auto_start_planner,
            ip_whitelists=ip_whitelists,
            rate_limits=rate_limits,
        )
        if persist_key:
            config_repo.save(ConfigDB(key=persist_key, value_json=json.dumps(new_config)))
        return new_config

    def verify_signature(self, *, source: str, payload_raw: bytes, signature: str) -> bool:
        return self._engine().verify_webhook_signature(source, payload_raw, signature)

    def process_webhook(self, *, source: str, payload: dict, headers: dict | None = None, client_ip: str | None = None) -> dict:
        return self._engine().process_webhook(source, payload, headers or {}, client_ip=client_ip)

    def preview_tasks(self, *, source: str, payload: dict) -> list[dict]:
        engine = self._engine()
        handler = engine._handlers.get(source)
        if handler:
            return list(handler(payload, {}))
        return list(engine._default_handler(source, payload))


trigger_runtime_service = TriggerRuntimeService()


def get_trigger_runtime_service() -> TriggerRuntimeService:
    return trigger_runtime_service

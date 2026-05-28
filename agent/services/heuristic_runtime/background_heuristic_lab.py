"""Background Heuristic Lab — LLM-gestützte Heuristik-Kandidaten im Hintergrund.

Entkoppelt vom TUI-Fast-Path: läuft async, blockiert nie die UI.
LLMs dürfen nur DSL-Kandidaten erzeugen, keine direkten Snake-Koordinaten.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_log = logging.getLogger(__name__)

_DEFAULT_MAX_PROPOSALS = 10
_DEFAULT_INTERVAL_S = 30.0


@dataclass
class LabConfig:
    enabled: bool = False  # Default: sicher deaktiviert
    model_backend: str = "lmstudio"
    interval_seconds: float = _DEFAULT_INTERVAL_S
    max_proposals: int = _DEFAULT_MAX_PROPOSALS
    snapshot_pack_size: int = 3
    auto_experiment_mode: bool = False  # Default: shadow-only


class BackgroundHeuristicLab:
    """Hintergrund-Service der LLM-Kandidaten erzeugt und validiert.

    Wird NICHT im TUI-Render-Loop aufgerufen.
    """

    def __init__(self, config: LabConfig | None = None) -> None:
        self._config = config or LabConfig()
        self._pending_proposals: list[dict[str, Any]] = []
        self._running = False
        self._last_run_at: float | None = None
        self._llm_client: Any = None  # wird extern injiziert

    def is_enabled(self) -> bool:
        return self._config.enabled

    def set_llm_client(self, client: Any) -> None:
        self._llm_client = client

    def get_pending_proposals(self) -> list[dict[str, Any]]:
        return list(self._pending_proposals)

    def clear_proposals(self) -> None:
        self._pending_proposals.clear()

    async def run_cycle(self, obs_pack: dict[str, Any]) -> None:
        """Führt einen Lab-Zyklus aus: Snapshot analysieren → LLM → validieren → speichern.

        Blockiert nie den UI-Thread (async).
        """
        if not self._config.enabled:
            _log.debug("HeuristicLab disabled")
            return

        if self._llm_client is None:
            _log.debug("HeuristicLab: kein LLM-Client konfiguriert")
            return

        if len(self._pending_proposals) >= self._config.max_proposals:
            _log.debug("HeuristicLab: max_proposals erreicht")
            return

        _log.info("HeuristicLab: starte Zyklus mit obs_pack size=%d", len(obs_pack))
        self._last_run_at = time.monotonic()

        try:
            raw_response = await self._call_llm(obs_pack)
            if raw_response:
                proposal = self._parse_and_validate(raw_response, obs_pack)
                if proposal:
                    self._pending_proposals.append(proposal)
                    _log.info("HeuristicLab: neuer Kandidat gespeichert, total=%d", len(self._pending_proposals))
        except Exception as e:
            _log.warning("HeuristicLab Fehler (UI nicht betroffen): %s", e)

    async def _call_llm(self, obs_pack: dict[str, Any]) -> str | None:
        """Ruft LLM im Hintergrund auf. Wirft Exception wenn offline — wird caught."""
        if self._llm_client is None:
            return None
        prompt = self._build_prompt(obs_pack)
        if hasattr(self._llm_client, "complete_async"):
            return await self._llm_client.complete_async(prompt)
        elif hasattr(self._llm_client, "complete"):
            return await asyncio.get_event_loop().run_in_executor(None, self._llm_client.complete, prompt)
        return None

    def _build_prompt(self, obs_pack: dict[str, Any]) -> str:
        snapshots_summary = obs_pack.get("recent_snapshots") or []
        return (
            f"Erzeuge eine DSL v2 Heuristik für tui_snake basierend auf diesen TUI-Snapshots: "
            f"{snapshots_summary}. "
            "Antworte NUR mit validem JSON ohne Markdown-Fences. "
            "Pflichtfelder: dsl_version='2.0', observe, action, safety, provenance. "
            "Verboten: inline_code, network_access, secret_access, file_write."
        )

    def _parse_and_validate(self, raw: str, obs_pack: dict[str, Any]) -> dict[str, Any] | None:
        try:
            from agent.services.heuristic_runtime.llm_heuristic_parser import LlmHeuristicParser
            parser = LlmHeuristicParser()
            result = parser.parse(raw)
            if result is None:
                return None
            # Füge Provenance-Felder hinzu
            snap_hashes = [s.get("screen_hash", "") for s in (obs_pack.get("recent_snapshots") or [])]
            result.setdefault("provenance", {})["source_snapshot_hashes"] = snap_hashes
            result["_proposal_meta"] = {
                "created_at": time.time(),
                "model": self._config.model_backend,
                "status": "candidate",
            }
            return result
        except Exception as e:
            _log.debug("HeuristicLab Parse-Fehler: %s", e)
            return None

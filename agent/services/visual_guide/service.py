"""VisualGuideService — business logic for the Visual Guide Engine."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from agent.adapters import visual_guide_route_bridge as _route_bridge
from agent.services.visual_guide.decision_service import VisualGuideDecisionService
from agent.services.visual_guide.models import (
    VisualGuideAction,
    VisualGuideDecision,
    VisualGuideRequest,
)
from agent.services.visual_guide.rule_engine import RuleEngine
from agent.services.visual_guide.trace_service import VisualGuideTraceService

_log = logging.getLogger(__name__)

# ── Constants re-exported from snakes_execution_routes ────────────────────────
# Defining them here avoids importing that module at module-init time (circular
# import risk) while still giving tests an easy single-module patch target.
_VISUAL_SESSION_ID: str = "ananta-visual"
_VISUAL_THROTTLE_S: float = 25.0


# ── Bridge callables — patched by tests; delegated to snakes_execution_routes ─

def _background_threads_disabled() -> bool:
    return _route_bridge.background_threads_disabled()


def _visual_session_settings() -> dict:
    return _route_bridge.visual_session_settings()


def _append_room_ai_message(**kwargs: Any) -> None:
    return _route_bridge.append_room_ai_message(**kwargs)


def _broadcast_snake_event(snake_id: str, event_type: str, payload: dict[str, Any]) -> None:
    _route_bridge.broadcast_snake_event(snake_id, event_type, payload)


# ── Rate limiting ──────────────────────────────────────────────────────────────
_RATE_LIMIT_LLM_CALLS_PER_MINUTE = 4

# ── Privacy redaction ─────────────────────────────────────────────────────────
_SENSITIVE_PATTERNS = [
    r'focus:(?:input|textarea)\[(?:password|token|api.?key|auth|secret|credential)[^\]]*\]="[^"]*"',
    r'focus:(?:input|textarea)\[[^\]]*\]="[A-Za-z0-9+/]{20,}={0,2}"',  # base64-artige Werte
]
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _SENSITIVE_PATTERNS]

# ── Per-snake visual state (VG-003) ───────────────────────────────────────────
# Authoritative state dict lives here (not in snakes_execution_routes) to avoid
# circular-import issues. snakes_execution_routes imports helpers from this module.
#
# Each entry: {"delta_snapshot": str, "reply_snapshot": str, "reply_at": float, "updated_at": float}
_visual_state: dict[str, dict] = {}
_MAX_VISUAL_STATES = 50
_VISUAL_STATE_TTL_S = 7200.0  # 2 hours


def _get_visual_state(snake_id: str) -> dict:
    """Return the mutable state dict for snake_id, enforcing TTL and max-50 cap.

    The returned dict is a live reference into _visual_state — callers may
    mutate it to update state.

    NOTE: Uses the current module-level _visual_state at call time so that
    test code that replaces the module attribute is picked up correctly.
    """
    import agent.services.visual_guide.service as _self_mod
    vs: dict[str, dict] = _self_mod._visual_state
    now = time.time()
    # TTL eviction
    expired = [
        sid for sid, s in list(vs.items())
        if now - float(s.get("updated_at", 0)) > _VISUAL_STATE_TTL_S
    ]
    for sid in expired:
        vs.pop(sid, None)

    # Max-50 cap: evict oldest when full and the key is not yet present
    if snake_id not in vs and len(vs) >= _MAX_VISUAL_STATES:
        oldest = min(vs, key=lambda k: float(vs[k].get("updated_at", 0)))
        vs.pop(oldest, None)

    if snake_id not in vs:
        vs[snake_id] = {
            "delta_snapshot": "",
            "reply_snapshot": "",
            "reply_at": 0.0,
            "updated_at": now,
        }
    return vs[snake_id]


class VisualGuideService:
    """Encapsulates all business logic for the Visual Guide Engine."""

    def __init__(self) -> None:
        self._decision_svc = VisualGuideDecisionService()
        self._trace_svc = VisualGuideTraceService()
        self._rule_engine = RuleEngine()
        # Per-snake rate-limit timestamps
        self._rate_limit_timestamps: dict[str, list[float]] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def handle_ui_tick(
        self,
        snake_id: str,
        ui_snapshot: str,
        route: str,
        visible_waypoints: list,
    ) -> None:
        """Store the tick, compute delta, optionally spawn an AI reply."""
        # Use module-level bridge callables — tests can patch these on this module
        # without triggering the snakes_execution_routes circular import.
        import agent.services.visual_guide.service as _self_mod

        if _self_mod._background_threads_disabled():
            return

        pug = _self_mod._visual_session_settings()
        if not pug.get("predictive_guide_enabled", False):
            return

        snapshot = self._redact_snapshot(ui_snapshot)
        state = _get_visual_state(snake_id)

        # Reply throttle: same snapshot already processed
        if snapshot == state["reply_snapshot"]:
            return
        now = time.time()
        if now - state["reply_at"] < _self_mod._VISUAL_THROTTLE_S:
            return

        # Build request
        request = VisualGuideRequest(
            snake_id=snake_id,
            trigger_type="ui_tick",
            route=route,
            snapshot=snapshot,
        )

        # Decision
        decision = self._decision_svc.decide(request, pug)
        trace_id = self._trace_svc.start_trace(request)

        if decision.strategy == "suppressed":
            self._trace_svc.emit(trace_id, "suppressed_by_rate_limit", {"reason": decision.reason})
            self._trace_svc.finish_trace(trace_id, success=False)
            return

        # Update state before spawning so concurrent calls don't double-fire
        state["reply_snapshot"] = snapshot
        state["reply_at"] = now
        state["updated_at"] = now

        if decision.strategy == "rule":
            route_tip = self._rule_engine.lookup_route(route)
            if route_tip:
                self._trace_svc.emit(trace_id, "action_generated", {"strategy": "rule", "tip": route_tip})
                _self_mod._append_room_ai_message(
                    text=route_tip, session_id=_self_mod._VISUAL_SESSION_ID, visibility="room",
                )
                action = VisualGuideAction(
                    request_id=request.request_id,
                    trigger_type="ui_tick",
                    priority=7,
                )
                self._trace_svc.finish_trace(trace_id, success=True, action=action)
            return

        # LLM path — check rate limit
        if not self._check_rate_limit(snake_id):
            self._trace_svc.emit(trace_id, "suppressed_by_rate_limit", {"reason": "rate_limit_exceeded"})
            self._trace_svc.finish_trace(trace_id, success=False)
            return

        self._trace_svc.emit(trace_id, "model_invoked", {"strategy": "llm"})

        n_candidates = max(1, min(5, int(pug.get("predictive_guide_multi_candidates", 3))))
        try:
            answer = self._call_llm_for_ui_tick(snapshot, n_candidates, pug)
            if answer:
                _self_mod._append_room_ai_message(
                    text=answer, session_id=_self_mod._VISUAL_SESSION_ID, visibility="room",
                )
                _log.info("ananta-visual: reply appended (%d chars)", len(answer))

                # SSE push: candidates (multi-candidate mode) or guide steps
                candidates = self._parse_candidates(answer) if n_candidates > 1 else []
                if candidates:
                    _self_mod._broadcast_snake_event(
                        snake_id, "candidates",
                        {"request_id": request.request_id, "candidates": candidates},
                    )
                else:
                    guide_steps = self._extract_guide_steps(answer)
                    if guide_steps:
                        _self_mod._broadcast_snake_event(
                            snake_id, "guide",
                            {"request_id": request.request_id, "trigger_type": "ui_tick", "steps": guide_steps},
                        )

                action = VisualGuideAction(
                    request_id=request.request_id,
                    trigger_type="ui_tick",
                    priority=7,
                )
                self._trace_svc.emit(trace_id, "action_generated", {"chars": len(answer)})
                self._trace_svc.finish_trace(trace_id, success=True, action=action)
            else:
                self._trace_svc.finish_trace(trace_id, success=False)
        except Exception as exc:
            _log.warning("ananta-visual reply failed: %s", exc)
            self._trace_svc.emit(trace_id, "error", {"error": str(exc)[:200]})
            self._trace_svc.finish_trace(trace_id, success=False)

    def handle_region_explain(
        self,
        snake_id: str,
        region_steps: list[dict],
        route: str,
    ) -> None:
        """Validate steps, spawn AI explain reply."""
        import agent.services.visual_guide.service as _self_mod

        if _self_mod._background_threads_disabled():
            return

        _MAX_REGION_STEPS = 12
        region_steps = [s for s in region_steps if isinstance(s, dict)][:_MAX_REGION_STEPS]
        if not region_steps:
            return

        request = VisualGuideRequest(
            snake_id=snake_id,
            trigger_type="region_explain",
            route=route,
            region_steps=region_steps,
        )
        trace_id = self._trace_svc.start_trace(request)

        # Rule engine: try to resolve tips without LLM
        rule_bubbles: list[str | None] = []
        all_from_rules = True
        labels = [str(s.get("bubble") or "") for s in region_steps if s.get("bubble")]
        for lbl in labels:
            tip = self._rule_engine.lookup_region_step(lbl)
            rule_bubbles.append(tip)
            if tip is None:
                all_from_rules = False

        if all_from_rules and rule_bubbles:
            guide_steps = self._build_guide_steps(region_steps, [b or "" for b in rule_bubbles])
            if guide_steps:
                guide_json = json.dumps({"steps": guide_steps})
                _n = len(guide_steps)
                _summary = f"Guide gestartet: {_n} {'Schritt' if _n == 1 else 'Schritte'} auf {route or '(unbekannt)'} (Regel)"
                _self_mod._append_room_ai_message(
                    text=f"{_summary}\n\n__GUIDE__:{guide_json}",
                    session_id=_self_mod._VISUAL_SESSION_ID,
                    visibility="room",
                )
                _self_mod._broadcast_snake_event(
                    snake_id, "guide",
                    {"request_id": request.request_id, "trigger_type": "region_explain", "steps": guide_steps},
                )
                action = VisualGuideAction(
                    request_id=request.request_id,
                    trigger_type="region_explain",
                    priority=2,
                    guide_steps=guide_steps,
                )
                self._trace_svc.emit(trace_id, "action_generated", {"strategy": "rule", "steps": len(guide_steps)})
                self._trace_svc.finish_trace(trace_id, success=True, action=action)
                return

        # LLM path
        if not self._check_rate_limit(snake_id):
            self._trace_svc.emit(trace_id, "suppressed_by_rate_limit", {"reason": "rate_limit_exceeded"})
            self._trace_svc.finish_trace(trace_id, success=False)
            return

        try:
            explanations = self._call_llm_for_region_explain(region_steps, route)
            guide_steps = self._build_guide_steps(region_steps, explanations)
            if not guide_steps:
                self._trace_svc.finish_trace(trace_id, success=False)
                return

            guide_json = json.dumps({"steps": guide_steps})
            _n = len(guide_steps)
            _summary = f"Guide gestartet: {_n} {'Schritt' if _n == 1 else 'Schritte'} auf {route or '(unbekannt)'}"
            _self_mod._append_room_ai_message(
                text=f"{_summary}\n\n__GUIDE__:{guide_json}",
                session_id=_self_mod._VISUAL_SESSION_ID,
                visibility="room",
            )
            _self_mod._broadcast_snake_event(
                snake_id, "guide",
                {"request_id": request.request_id, "trigger_type": "region_explain", "steps": guide_steps},
            )
            _log.info("region-explain: guide reply with %d steps appended", len(guide_steps))
            action = VisualGuideAction(
                request_id=request.request_id,
                trigger_type="region_explain",
                priority=2,
                guide_steps=guide_steps,
            )
            self._trace_svc.emit(trace_id, "action_generated", {"strategy": "llm", "steps": len(guide_steps)})
            self._trace_svc.finish_trace(trace_id, success=True, action=action)
        except Exception as exc:
            _log.warning("region-explain reply failed: %s", exc)
            self._trace_svc.emit(trace_id, "error", {"error": str(exc)[:200]})
            self._trace_svc.finish_trace(trace_id, success=False)

    # ── Rate limiting (VG-051) ─────────────────────────────────────────────────

    def _check_rate_limit(self, snake_id: str) -> bool:
        """Returns True when rate limit has not been reached."""
        now = time.time()
        ts = self._rate_limit_timestamps.setdefault(snake_id, [])
        # Remove timestamps older than 60s
        ts[:] = [t for t in ts if now - t < 60]
        if len(ts) >= _RATE_LIMIT_LLM_CALLS_PER_MINUTE:
            return False
        ts.append(now)
        return True

    # ── Privacy (VG-052) ───────────────────────────────────────────────────────

    def _redact_snapshot(self, snapshot: str) -> str:
        """Mask sensitive input values in the snapshot before sending to LLM or Trace."""
        result = str(snapshot or "")
        for pattern in _COMPILED_PATTERNS:
            result = pattern.sub(lambda m: re.sub(r'="[^"]*"', '="[REDACTED]"', m.group()), result)
        return result[:500]

    # ── LLM helpers (VG-011) ──────────────────────────────────────────────────

    def _call_llm_for_ui_tick(self, snapshot: str, n_candidates: int, pug: dict) -> str:
        """Call LLM via ModelInvocationService to generate guide reply for ui_tick."""
        _cfg = _route_bridge.current_ai_snake_config()
        model = str(_cfg.get("chat_model") or "gpt-4o-mini") or None

        if n_candidates == 1:
            system_prompt = (
                "Du bist die orangene Guide-Snake in der Ananta App — eine kleine KI-Schlange "
                "die den User visuell durch die App führt.\n"
                "Du bekommst den aktuellen UI-Zustand als kompakten Text.\n"
                "Reagiere in 1-2 kurzen deutschen Sätzen auf das was der User gerade sieht.\n"
                'Füge wenn sinnvoll __GUIDE__: Steps an (JSON, Format: {"steps":[{"waypoint":"...","bubble":"...","delay_ms":3000}]}).\n'
                "Wenn der Zustand trivial/unklar ist, antworte mit leerem Text."
            )
        else:
            system_prompt = (
                f"Du bist die orangene Guide-Snake in der Ananta App.\n"
                f"Generiere genau {n_candidates} alternative Guide-Vorschläge für den aktuellen UI-Zustand.\n"
                f"Antworte NUR mit folgendem JSON (kein weiterer Text davor oder danach):\n"
                f'__CANDIDATES__: [{{"label":"primary","bubble":"<Guide-Satz auf Deutsch>",'
                f'"steps":[{{"waypoint":"...","bubble":"...","delay_ms":3000}}]}},'
                f'{{"label":"alt-1","bubble":"<Alternative>","steps":[]}}]\n'
                f"Wenn der Zustand trivial ist, antworte mit: __CANDIDATES__: []"
            )
        user_msg = f"Aktueller UI-Zustand:\n{snapshot}"

        # VG-011: use ModelInvocationService
        try:
            from agent.services.model_invocation_service import ModelInvocationService
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
            response = ModelInvocationService._make_chat_call(
                messages,
                model=model,
                timeout=30,
            )
            choice = (response.get("choices") or [{}])[0]
            return ((choice.get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            # TODO: ModelInvocationService may not have correct provider config for visual guide;
            # falling back to direct openai call
            _log.debug("ModelInvocationService failed for ui_tick, falling back to openai: %s", exc)
            return self._call_openai_fallback_ui_tick(snapshot, n_candidates, _cfg)

    def _call_openai_fallback_ui_tick(self, snapshot: str, n_candidates: int, cfg: dict) -> str:
        """Direct openai fallback for ui_tick when ModelInvocationService is unavailable."""
        import openai as _oai
        model = str(cfg.get("chat_model") or "gpt-4o-mini")
        api_base = str(cfg.get("chat_api_base") or "")
        api_key = str(cfg.get("chat_api_key") or os.environ.get("OPENAI_API_KEY") or "")

        if n_candidates == 1:
            system_prompt = (
                "Du bist die orangene Guide-Snake in der Ananta App.\n"
                "Reagiere in 1-2 kurzen deutschen Sätzen auf den UI-Zustand.\n"
                'Füge wenn sinnvoll __GUIDE__: Steps an.\n'
                "Wenn trivial/unklar: leerer Text."
            )
        else:
            system_prompt = (
                f"Du bist die orangene Guide-Snake in der Ananta App.\n"
                f"Generiere genau {n_candidates} alternative Guide-Vorschläge.\n"
                f"Antworte NUR mit JSON: __CANDIDATES__: [...]"
            )

        _client_kwargs: dict[str, Any] = {"api_key": api_key or "sk-no-key"}
        if api_base:
            _client_kwargs["base_url"] = api_base
        _client = _oai.OpenAI(**_client_kwargs)
        _resp = _client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Aktueller UI-Zustand:\n{snapshot}"},
            ],
            max_tokens=400 if n_candidates > 1 else 200,
            temperature=0.4,
        )
        return (_resp.choices[0].message.content or "").strip()

    def _call_llm_for_region_explain(self, region_steps: list[dict], route: str) -> list[str]:
        """Call LLM via ModelInvocationService to get explanations for region steps."""
        _cfg = _route_bridge.current_ai_snake_config()
        model = str(_cfg.get("chat_model") or "gpt-4o-mini") or None

        labels = [str(s.get("bubble") or "") for s in region_steps if s.get("bubble")]
        if not labels:
            return []

        elements_list = "\n".join(f"{i+1}. {lbl}" for i, lbl in enumerate(labels))
        system_prompt = (
            "Du bist die orangene Guide-Snake in der Ananta App.\n"
            "Der User hat eine Region auf der Seite markiert und möchte kurze Erklärungen.\n"
            "Gib für jedes Element EINE kurze Erklärung auf Deutsch (max 12 Wörter).\n"
            "Antworte NUR mit einem JSON-Array, genau so viele Einträge wie Elemente:\n"
            '["Erklärung für Element 1", "Erklärung für Element 2", ...]'
        )
        user_msg = f"Seite: {route or '(unbekannt)'}\n\nAusgewählte Elemente:\n{elements_list}"

        raw = ""
        # VG-011: use ModelInvocationService
        try:
            from agent.services.model_invocation_service import ModelInvocationService
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
            response = ModelInvocationService._make_chat_call(
                messages,
                model=model,
                timeout=30,
            )
            choice = (response.get("choices") or [{}])[0]
            raw = ((choice.get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            # TODO: ModelInvocationService may not have correct provider config for visual guide;
            # falling back to direct openai call
            _log.debug("ModelInvocationService failed for region_explain, falling back: %s", exc)
            raw = self._call_openai_fallback_region_explain(system_prompt, user_msg, _cfg)

        # Parse JSON array from response
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            _log.warning("region-explain: LLM returned no JSON array: %r", raw[:120])
            return []
        explanations = json.loads(json_match.group())
        if not isinstance(explanations, list):
            return []
        return [str(e) for e in explanations]

    def _call_openai_fallback_region_explain(self, system_prompt: str, user_msg: str, cfg: dict) -> str:
        """Direct openai fallback for region_explain when ModelInvocationService is unavailable."""
        import openai as _oai
        model = str(cfg.get("chat_model") or "gpt-4o-mini")
        api_base = str(cfg.get("chat_api_base") or "")
        api_key = str(cfg.get("chat_api_key") or os.environ.get("OPENAI_API_KEY") or "")

        _client_kwargs: dict[str, Any] = {"api_key": api_key or "sk-no-key"}
        if api_base:
            _client_kwargs["base_url"] = api_base
        _client = _oai.OpenAI(**_client_kwargs)
        _resp = _client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return (_resp.choices[0].message.content or "").strip()

    @staticmethod
    def _build_guide_steps(region_steps: list[dict], explanations: list[str]) -> list[dict]:
        """Merge region steps with explanations into guide steps."""
        guide_steps = []
        for i, step in enumerate(region_steps):
            bubble = explanations[i].strip() if i < len(explanations) else str(step.get("bubble") or "")
            if not bubble:
                continue
            try:
                x = float(step.get("x") or 0)
                y = float(step.get("y") or 0)
                if not (0 <= x <= 10000 and 0 <= y <= 10000):
                    continue
            except (TypeError, ValueError):
                continue
            guide_steps.append({
                "waypoint": str(step.get("waypoint") or "__region__"),
                "bubble": bubble[:120],
                "delay_ms": 3500,
                "x": x,
                "y": y,
            })
        return guide_steps

    @staticmethod
    def _extract_guide_steps(text: str) -> list[dict]:
        """Parse __GUIDE__: JSON from an answer and return the steps list."""
        marker = "__GUIDE__:"
        idx = text.find(marker)
        if idx < 0:
            return []
        raw = text[idx + len(marker):].strip()
        # Stop at first newline after the JSON object so trailing text is ignored
        nl = raw.find("\n")
        if nl > 0:
            raw = raw[:nl]
        try:
            parsed = json.loads(raw)
            steps = list(parsed.get("steps") or [])
            return [s for s in steps if isinstance(s, dict)]
        except Exception:
            return []

    def handle_manual_guide(self, snake_id: str, intent: str, snapshot: str = "", route: str = "") -> None:
        """Handle a /guide chat command — spawn a guide based on explicit user intent."""
        import agent.services.visual_guide.service as _self_mod

        if _self_mod._background_threads_disabled():
            return
        if not intent:
            return

        request = VisualGuideRequest(
            snake_id=snake_id,
            trigger_type="manual",
            route=route,
            snapshot=snapshot,
        )
        trace_id = self._trace_svc.start_trace(request)

        if not self._check_rate_limit(snake_id):
            _self_mod._append_room_ai_message(
                text="Guide-Rate-Limit erreicht. Bitte kurz warten.",
                session_id=_self_mod._VISUAL_SESSION_ID,
                visibility="room",
            )
            self._trace_svc.emit(trace_id, "suppressed_by_rate_limit", {"reason": "rate_limit_exceeded"})
            self._trace_svc.finish_trace(trace_id, success=False)
            return

        self._trace_svc.emit(trace_id, "model_invoked", {"strategy": "llm", "intent": intent[:80]})

        try:
            answer = self._call_llm_for_manual_guide(intent, snapshot, route)
            if answer:
                _self_mod._append_room_ai_message(
                    text=answer,
                    session_id=_self_mod._VISUAL_SESSION_ID,
                    visibility="room",
                )
                guide_steps = self._extract_guide_steps(answer)
                if guide_steps:
                    _self_mod._broadcast_snake_event(
                        snake_id, "guide",
                        {"request_id": request.request_id, "trigger_type": "manual", "steps": guide_steps},
                    )
                action = VisualGuideAction(
                    request_id=request.request_id,
                    trigger_type="manual",
                    priority=5,
                    guide_steps=guide_steps if guide_steps else [],
                )
                self._trace_svc.emit(trace_id, "action_generated", {"chars": len(answer), "guide_steps": len(guide_steps)})
                self._trace_svc.finish_trace(trace_id, success=True, action=action)
            else:
                self._trace_svc.finish_trace(trace_id, success=False)
        except Exception as exc:
            _log.warning("manual guide failed: %s", exc)
            self._trace_svc.emit(trace_id, "error", {"error": str(exc)[:200]})
            self._trace_svc.finish_trace(trace_id, success=False)

    def _call_llm_for_manual_guide(self, intent: str, snapshot: str, route: str) -> str:
        """Call LLM to generate guide steps for an explicit /guide intent."""
        _cfg = _route_bridge.current_ai_snake_config()
        model = str(_cfg.get("chat_model") or "gpt-4o-mini") or None

        system_prompt = (
            "Du bist die orangene Guide-Snake in der Ananta App.\n"
            "Der User hat explizit einen Guide angefordert.\n"
            "Antworte auf Deutsch: 1 einleitender Satz, danach __GUIDE__: Steps.\n"
            'Format: __GUIDE__:{"steps":[{"waypoint":"...","bubble":"...","delay_ms":3000}]}\n'
            "Wenn du keine passenden Schritte kennst, erkläre das ohne Guide-Steps."
        )
        ctx_parts = []
        if snapshot:
            ctx_parts.append(f"UI-Zustand: {snapshot[:300]}")
        if route:
            ctx_parts.append(f"Route: {route}")
        ctx = "\n".join(ctx_parts) if ctx_parts else "(kein UI-Kontext)"
        user_msg = f"Guide-Intent: {intent}\n\n{ctx}"

        try:
            from agent.services.model_invocation_service import ModelInvocationService
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
            response = ModelInvocationService._make_chat_call(messages, model=model, timeout=30)
            choice = (response.get("choices") or [{}])[0]
            return ((choice.get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            _log.debug("ModelInvocationService failed for manual guide, falling back: %s", exc)
            return self._call_openai_fallback_manual_guide(system_prompt, user_msg, _cfg)

    def _call_openai_fallback_manual_guide(self, system_prompt: str, user_msg: str, cfg: dict) -> str:
        """Direct openai fallback for manual guide when ModelInvocationService is unavailable."""
        import openai as _oai
        model = str(cfg.get("chat_model") or "gpt-4o-mini")
        api_base = str(cfg.get("chat_api_base") or "")
        api_key = str(cfg.get("chat_api_key") or os.environ.get("OPENAI_API_KEY") or "")

        _client_kwargs: dict[str, Any] = {"api_key": api_key or "sk-no-key"}
        if api_base:
            _client_kwargs["base_url"] = api_base
        _client = _oai.OpenAI(**_client_kwargs)
        _resp = _client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.4,
        )
        return (_resp.choices[0].message.content or "").strip()

    @staticmethod
    def _parse_candidates(text: str) -> list[dict]:
        """Parse __CANDIDATES__: JSON from an answer.

        Expected shape: [{"label":"...","bubble":"...","steps":[...]}, ...]
        Returns an empty list when the marker is missing or parsing fails.
        """
        marker = "__CANDIDATES__:"
        idx = text.find(marker)
        if idx < 0:
            return []
        raw = text[idx + len(marker):].strip()
        nl = raw.find("\n")
        if nl > 0:
            raw = raw[:nl]
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return []
            return [c for c in parsed if isinstance(c, dict) and c.get("label")]
        except Exception:
            return []


# ── Module-level singleton ────────────────────────────────────────────────────

_visual_guide_service = VisualGuideService()

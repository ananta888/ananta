from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from agent.services.propose_policy import ProposePolicy
from agent.services.planning_contract import resolve_planning_contract
from agent.services.planning_validation_service import get_planning_validation_service

ERR_TIMEOUT = "context_compactor_timeout"
ERR_UNPARSEABLE = "context_compactor_unparseable_output"
ERR_SCHEMA = "context_compactor_schema_violation"
ERR_CONSTRAINT_LOSS = "context_compactor_constraint_loss_detected"
ERR_RUNTIME = "context_compactor_runtime_unavailable"
ERR_CONTRACT_CONSTRAINT_LOSS = "context_compactor_contract_constraint_loss"

_HEAVY_FIELDS = {"logs", "history", "raw_output", "stdout", "stderr", "traces", "artifacts", "diffs", "context_dump"}


@dataclass(frozen=True)
class CompactionResult:
    payload: dict[str, Any]
    meta: dict[str, Any]


class PlanningContextCompactorService:
    @staticmethod
    def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload or {})
        for list_key in ("hard_constraints", "non_negotiables", "relevant_context", "risks", "open_questions"):
            value = normalized.get(list_key)
            if not isinstance(value, list):
                normalized[list_key] = []
                continue
            normalized[list_key] = [str(item).strip() for item in value if str(item).strip()]
        normalized["goal_summary"] = str(normalized.get("goal_summary") or "").strip()
        normalized["omitted_context_summary"] = str(normalized.get("omitted_context_summary") or "").strip()
        return normalized

    def _trim_text(self, value: str, *, max_chars: int) -> str:
        raw = str(value or "")
        if len(raw) <= max_chars:
            return raw
        head = max_chars // 2
        tail = max_chars - head
        return raw[:head] + "\n...[truncated]...\n" + raw[-tail:]

    def _pre_trim(self, value: Any, *, max_chars: int, max_items: int, truncated_fields: list[str], path: str = "root") -> Any:
        if isinstance(value, str):
            trimmed = self._trim_text(value, max_chars=max_chars)
            if trimmed != value:
                truncated_fields.append(path)
            return trimmed
        if isinstance(value, list):
            out = [self._pre_trim(v, max_chars=max_chars, max_items=max_items, truncated_fields=truncated_fields, path=f"{path}[{idx}]") for idx, v in enumerate(value[:max_items])]
            if len(value) > max_items:
                truncated_fields.append(path)
                out.append({"_omitted_items": len(value) - max_items})
            return out
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for idx, (k, v) in enumerate(value.items()):
                if idx >= max_items:
                    truncated_fields.append(path)
                    out["_omitted_keys"] = max(0, len(value) - max_items)
                    break
                key = str(k)
                child_path = f"{path}.{key}"
                if key.lower() in _HEAVY_FIELDS and isinstance(v, str):
                    out[key] = self._trim_text(v, max_chars=max_chars)
                    if out[key] != v:
                        truncated_fields.append(child_path)
                    continue
                out[key] = self._pre_trim(v, max_chars=max_chars, max_items=max_items, truncated_fields=truncated_fields, path=child_path)
            return out
        return value

    def _deterministic_constraints(self, goal_text: str, context_text: str, preserve_keywords: list[str]) -> tuple[list[str], list[str]]:
        blob = f"{goal_text}\n{context_text}".lower()
        hard: list[str] = []
        non_neg: list[str] = []
        for kw in preserve_keywords:
            if kw in blob:
                hard.append(f"preserve:{kw}")
                non_neg.append(f"must_keep_{kw}")
        if not hard:
            hard = ["preserve:constraints"]
            non_neg = ["must_keep_constraints"]
        return list(dict.fromkeys(hard)), list(dict.fromkeys(non_neg))

    def _base_payload(self, *, goal_text: str, context_text: str, hard: list[str], non_neg: list[str], trimmed_mode_data: Any) -> dict[str, Any]:
        relevant = []
        if goal_text:
            relevant.append(self._trim_text(goal_text, max_chars=1200))
        if context_text:
            relevant.append(self._trim_text(context_text, max_chars=2500))
        if trimmed_mode_data:
            relevant.append(self._trim_text(json.dumps(trimmed_mode_data, ensure_ascii=False), max_chars=2500))
        return {
            "goal_summary": self._trim_text(goal_text, max_chars=2000),
            "hard_constraints": hard,
            "non_negotiables": non_neg,
            "relevant_context": [item for item in relevant if item],
            "omitted_context_summary": "deterministic_compaction",
            "risks": [],
            "open_questions": [],
        }

    def _validate_payload(self, payload: dict[str, Any], *, max_output_chars: int, hard_constraints: list[str], non_negotiables: list[str]) -> tuple[bool, str | None]:
        required = [
            "goal_summary",
            "hard_constraints",
            "non_negotiables",
            "relevant_context",
            "omitted_context_summary",
            "risks",
            "open_questions",
        ]
        for key in required:
            if key not in payload:
                return False, ERR_SCHEMA
        if not isinstance(payload.get("hard_constraints"), list) or not isinstance(payload.get("non_negotiables"), list):
            return False, ERR_SCHEMA
        if len(json.dumps(payload, ensure_ascii=False)) > max_output_chars:
            return False, ERR_SCHEMA

        content_blob = json.dumps(payload, ensure_ascii=False).lower()
        for marker in hard_constraints + non_negotiables:
            token = marker.split(":", 1)[-1].replace("must_keep_", "")
            if token and token not in content_blob:
                return False, ERR_CONTRACT_CONSTRAINT_LOSS
        return True, None

    def _llm_compact(self, *, trimmed_input: dict[str, Any], policy: ProposePolicy, llm_config: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
        cfg = dict(llm_config or {})
        provider = str(cfg.get("provider") or "").strip() or None
        model = str(cfg.get("model") or "").strip() or None
        base_url = str(cfg.get("base_url") or "").strip() or None
        timeout = int(policy.context_compactor_timeout_seconds)
        prompt = (
            "Return strict JSON object only with keys: goal_summary, hard_constraints, non_negotiables, "
            "relevant_context, omitted_context_summary, risks, open_questions.\n"
            f"INPUT={json.dumps(trimmed_input, ensure_ascii=False)}"
        )
        started = time.time()
        try:
            from agent.services.hub_llm_service import generate_text

            raw = generate_text(
                prompt=prompt,
                provider=provider,
                model=model,
                base_url=base_url,
                timeout=timeout,
                temperature=0.1,
            )
            text = str(raw.get("text") if isinstance(raw, dict) else raw or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return None, ERR_UNPARSEABLE, {
                    "provider": provider,
                    "model": model,
                    "duration_ms": int((time.time() - started) * 1000),
                }
            return parsed, None, {
                "provider": provider,
                "model": model,
                "duration_ms": int((time.time() - started) * 1000),
            }
        except TimeoutError:
            return None, ERR_TIMEOUT, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}
        except Exception:
            return None, ERR_RUNTIME, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}

    def _llm_repair_compact(
        self,
        *,
        trimmed_input: dict[str, Any],
        policy: ProposePolicy,
        llm_config: dict[str, Any] | None,
        broken_payload: dict[str, Any] | None,
        error_classification: str | None,
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
        cfg = dict(llm_config or {})
        provider = str(cfg.get("provider") or "").strip() or None
        model = str(cfg.get("model") or "").strip() or None
        base_url = str(cfg.get("base_url") or "").strip() or None
        timeout = int(policy.context_compactor_timeout_seconds)
        prompt = (
            "Repair the invalid compacted planning context and return strict JSON only "
            "with keys: goal_summary, hard_constraints, non_negotiables, relevant_context, "
            "omitted_context_summary, risks, open_questions.\n"
            f"ERROR_CLASS={error_classification}\n"
            f"BROKEN={json.dumps(broken_payload or {}, ensure_ascii=False)}\n"
            f"INPUT={json.dumps(trimmed_input, ensure_ascii=False)}"
        )
        started = time.time()
        try:
            from agent.services.hub_llm_service import generate_text

            raw = generate_text(
                prompt=prompt,
                provider=provider,
                model=model,
                base_url=base_url,
                timeout=timeout,
                temperature=0.1,
            )
            text = str(raw.get("text") if isinstance(raw, dict) else raw or "").strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return None, ERR_UNPARSEABLE, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}
            return parsed, None, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}
        except TimeoutError:
            return None, ERR_TIMEOUT, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}
        except Exception:
            return None, ERR_RUNTIME, {"provider": provider, "model": model, "duration_ms": int((time.time() - started) * 1000)}

    def compact(
        self,
        *,
        goal_text: str,
        context_text: str | None,
        mode: str,
        mode_data: dict | None,
        planning_policy: dict[str, Any] | None,
        llm_config: dict[str, Any] | None,
        policy: ProposePolicy,
    ) -> CompactionResult:
        goal_text = str(goal_text or "").strip()
        context_text = str(context_text or "").strip()
        started = time.time()
        truncated_fields: list[str] = []

        trimmed_mode_data = self._pre_trim(
            mode_data or {},
            max_chars=max(500, min(5000, int(policy.context_compactor_max_output_chars // 4))),
            max_items=40,
            truncated_fields=truncated_fields,
            path="mode_data",
        )
        hard, non_neg = self._deterministic_constraints(
            goal_text,
            context_text + "\n" + json.dumps(trimmed_mode_data, ensure_ascii=False),
            preserve_keywords=list(policy.context_compactor_preserve_keywords or []),
        )
        deterministic = self._base_payload(
            goal_text=goal_text,
            context_text=context_text,
            hard=hard,
            non_neg=non_neg,
            trimmed_mode_data=trimmed_mode_data,
        )
        input_chars = len(goal_text) + len(context_text) + len(json.dumps(mode_data or {}, ensure_ascii=False))

        status = "deterministic_only"
        error_classification = None
        provider = None
        model = None
        duration_ms = 0
        final_payload = deterministic

        trimmed_input = {
            "goal": goal_text,
            "context": context_text,
            "mode": mode,
            "mode_data": trimmed_mode_data,
            "hard_constraints": hard,
            "non_negotiables": non_neg,
        }
        if bool(policy.context_compaction_enabled):
            retries = int(policy.context_compactor_retry_attempts)
            for _ in range(retries + 1):
                llm_payload, llm_error, llm_meta = self._llm_compact(
                    trimmed_input=trimmed_input,
                    policy=policy,
                    llm_config=llm_config,
                )
                provider = llm_meta.get("provider")
                model = llm_meta.get("model")
                duration_ms = int(llm_meta.get("duration_ms") or 0)
                if llm_payload is None:
                    error_classification = llm_error
                    continue
                llm_payload = self._normalize_payload(llm_payload)
                ok, err = self._validate_payload(
                    llm_payload,
                    max_output_chars=int(policy.context_compactor_max_output_chars),
                    hard_constraints=hard,
                    non_negotiables=non_neg,
                )
                if ok:
                    final_payload = llm_payload
                    status = "success"
                    error_classification = None
                    break
                error_classification = err
                repair_payload, repair_error, repair_meta = self._llm_repair_compact(
                    trimmed_input=trimmed_input,
                    policy=policy,
                    llm_config=llm_config,
                    broken_payload=llm_payload,
                    error_classification=error_classification,
                )
                provider = repair_meta.get("provider") or provider
                model = repair_meta.get("model") or model
                duration_ms = int(repair_meta.get("duration_ms") or duration_ms)
                if repair_payload is None:
                    error_classification = repair_error or error_classification
                    continue
                repair_payload = self._normalize_payload(repair_payload)
                ok, err = self._validate_payload(
                    repair_payload,
                    max_output_chars=int(policy.context_compactor_max_output_chars),
                    hard_constraints=hard,
                    non_negotiables=non_neg,
                )
                if ok:
                    final_payload = repair_payload
                    status = "success"
                    error_classification = None
                    break
                error_classification = err

        if status != "success":
            ok, err = self._validate_payload(
                deterministic,
                max_output_chars=int(policy.context_compactor_max_output_chars),
                hard_constraints=hard,
                non_negotiables=non_neg,
            )
            if ok:
                status = "fallback"
                final_payload = deterministic
                error_classification = error_classification or err
            elif policy.context_compactor_fail_open and not policy.context_compaction_required:
                status = "bypassed"
                final_payload = self._base_payload(
                    goal_text=goal_text,
                    context_text=context_text,
                    hard=hard,
                    non_neg=non_neg,
                    trimmed_mode_data={},
                )
            else:
                status = "failed"
                final_payload = deterministic
                error_classification = error_classification or err or ERR_SCHEMA

        output_chars = len(json.dumps(final_payload, ensure_ascii=False))
        contract = resolve_planning_contract(mode=mode, planning_policy=planning_policy)
        contract_validation = get_planning_validation_service().validate_subtasks(
            subtasks=[
                {
                    "title": "Compactor constraints preservation",
                    "description": " ".join(list(final_payload.get("hard_constraints") or []) + list(final_payload.get("non_negotiables") or [])),
                    "task_kind": "analysis",
                },
                {
                    "title": "Compactor review constraints",
                    "description": " ".join(list(final_payload.get("relevant_context") or [])),
                    "task_kind": "review",
                },
                {
                    "title": "Compactor testing constraints",
                    "description": str(final_payload.get("goal_summary") or ""),
                    "task_kind": "testing",
                },
                {
                    "title": "Compactor coding constraints",
                    "description": str(final_payload.get("omitted_context_summary") or ""),
                    "task_kind": "coding",
                },
            ],
            contract=contract,
        )
        if not contract_validation.ok and status != "bypassed":
            status = "failed"
            error_classification = ERR_CONTRACT_CONSTRAINT_LOSS
        reduction_ratio = 1.0 if input_chars <= 0 else round(max(0.0, min(1.0, output_chars / float(max(1, input_chars)))), 4)
        meta = {
            "input_chars": int(input_chars),
            "output_chars": int(output_chars),
            "reduction_ratio": reduction_ratio,
            "truncated_fields": list(dict.fromkeys(truncated_fields)),
            "provider": provider,
            "model": model,
            "duration_ms": int(duration_ms or ((time.time() - started) * 1000)),
            "status": status,
            "error_classification": error_classification,
            "fallback_stage": None if status == "success" else status,
        }
        payload = {**final_payload, "compactor_meta": meta}
        return CompactionResult(payload=payload, meta=meta)


_SERVICE = PlanningContextCompactorService()


def get_planning_context_compactor_service() -> PlanningContextCompactorService:
    return _SERVICE

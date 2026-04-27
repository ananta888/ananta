from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_VALID_ACTIONS = {"warn", "inject_correction", "require_review", "pause", "abort"}
_DEFAULT_SEVERITY_ACTIONS = {
    "low": "warn",
    "medium": "inject_correction",
    "high": "require_review",
    "critical": "pause",
}
_OUTCOME_LADDER = ["warn", "inject_correction", "require_review", "pause", "abort"]
_CLASSIFICATION_PRIORITY = {
    "oscillating_retry_pattern": 4,
    "repeated_tool_call": 3,
    "repeated_failure": 2,
    "no_progress": 1,
}


@dataclass(frozen=True)
class DoomLoopDecision:
    detected: bool
    classification: str
    severity: str
    action: str
    reasons: list[str]
    metrics: dict[str, int]
    signal_count: int
    policy: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "classification": self.classification,
            "severity": self.severity,
            "action": self.action,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "signal_count": self.signal_count,
            "policy": dict(self.policy),
            "outcome_ladder": list(_OUTCOME_LADDER),
        }


class DoomLoopService:
    """Generic signal collection and detector for repeated execution loops."""

    def normalize_policy(self, value: dict | None) -> dict[str, Any]:
        payload = dict(value or {})
        actions_raw = dict(payload.get("severity_actions") or {})
        severity_actions = dict(_DEFAULT_SEVERITY_ACTIONS)
        for key in ("low", "medium", "high", "critical"):
            candidate = str(actions_raw.get(key) or severity_actions[key]).strip().lower()
            severity_actions[key] = candidate if candidate in _VALID_ACTIONS else severity_actions[key]
        return {
            "enabled": bool(payload.get("enabled", True)),
            "lookback_signals": self._clamp_int(payload.get("lookback_signals"), default=40, minimum=8, maximum=200),
            "repeated_tool_call_threshold": self._clamp_int(payload.get("repeated_tool_call_threshold"), default=4, minimum=2, maximum=50),
            "repeated_failure_threshold": self._clamp_int(payload.get("repeated_failure_threshold"), default=4, minimum=2, maximum=50),
            "no_progress_threshold": self._clamp_int(payload.get("no_progress_threshold"), default=5, minimum=2, maximum=80),
            "oscillation_threshold": self._clamp_int(payload.get("oscillation_threshold"), default=4, minimum=4, maximum=50),
            "critical_abort_threshold": self._clamp_int(payload.get("critical_abort_threshold"), default=8, minimum=4, maximum=120),
            "severity_actions": severity_actions,
            "enforce_pause_abort": bool(payload.get("enforce_pause_abort", False)),
        }

    def build_signal(
        self,
        *,
        task_id: str | None,
        trace_id: str | None,
        backend_name: str | None,
        action_type: str | None,
        failure_type: str | None,
        iteration_count: int | None,
        action_signature: str | None = None,
        progress_made: bool | None = None,
    ) -> dict[str, Any]:
        failure = str(failure_type or "success").strip().lower() or "success"
        progress = bool(progress_made) if progress_made is not None else failure == "success"
        signal = {
            "task_id": str(task_id or "").strip() or None,
            "trace_id": str(trace_id or "").strip() or None,
            "backend_name": str(backend_name or "unknown").strip().lower() or "unknown",
            "action_type": str(action_type or "unknown").strip().lower() or "unknown",
            "failure_type": failure,
            "iteration_count": max(1, self._clamp_int(iteration_count, default=1, minimum=1, maximum=10000)),
            "action_signature": (str(action_signature or "").strip() or None),
            "progress_made": progress,
        }
        if signal["action_signature"] and len(signal["action_signature"]) > 260:
            signal["action_signature"] = signal["action_signature"][:260]
        return signal

    def collect_signals_from_history(self, history: list[dict] | None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for entry in list(history or []):
            if not isinstance(entry, dict):
                continue
            event_type = str(entry.get("event_type") or "").strip().lower()
            if event_type == "loop_signal" and isinstance(entry.get("signal"), dict):
                normalized = self._normalize_signal(entry.get("signal"))
                if normalized is not None:
                    records.append(normalized)
                continue
            loop_signals = entry.get("loop_signals")
            if isinstance(loop_signals, list):
                for signal in loop_signals:
                    normalized = self._normalize_signal(signal)
                    if normalized is not None:
                        records.append(normalized)
                continue
            normalized = self._normalize_signal(entry)
            if normalized is not None:
                records.append(normalized)
        return records

    def detect(self, *, signals: list[dict], policy: dict | None = None) -> DoomLoopDecision:
        normalized_policy = self.normalize_policy(policy)
        if not normalized_policy["enabled"]:
            return DoomLoopDecision(
                detected=False,
                classification="none",
                severity="none",
                action="none",
                reasons=[],
                metrics={},
                signal_count=0,
                policy=normalized_policy,
            )
        normalized_signals = [signal for signal in (self._normalize_signal(item) for item in list(signals or [])) if signal is not None]
        if not normalized_signals:
            return DoomLoopDecision(
                detected=False,
                classification="none",
                severity="none",
                action="none",
                reasons=[],
                metrics={},
                signal_count=0,
                policy=normalized_policy,
            )
        lookback = int(normalized_policy["lookback_signals"])
        recent = normalized_signals[-lookback:]

        repeated_tool_call = self._max_repeated_tool_calls(recent)
        repeated_failure = self._max_consecutive_failures(recent)
        no_progress = self._max_no_progress_streak(recent)
        oscillation = self._max_oscillation_streak(recent)

        triggered: list[tuple[str, float, str]] = []
        if repeated_tool_call >= int(normalized_policy["repeated_tool_call_threshold"]):
            triggered.append(
                (
                    "repeated_tool_call",
                    repeated_tool_call / float(normalized_policy["repeated_tool_call_threshold"]),
                    f"repeated_tool_call_streak={repeated_tool_call}",
                )
            )
        if repeated_failure >= int(normalized_policy["repeated_failure_threshold"]):
            triggered.append(
                (
                    "repeated_failure",
                    repeated_failure / float(normalized_policy["repeated_failure_threshold"]),
                    f"repeated_failure_streak={repeated_failure}",
                )
            )
        if no_progress >= int(normalized_policy["no_progress_threshold"]):
            triggered.append(
                (
                    "no_progress",
                    no_progress / float(normalized_policy["no_progress_threshold"]),
                    f"no_progress_streak={no_progress}",
                )
            )
        if oscillation >= int(normalized_policy["oscillation_threshold"]):
            triggered.append(
                (
                    "oscillating_retry_pattern",
                    oscillation / float(normalized_policy["oscillation_threshold"]),
                    f"oscillation_streak={oscillation}",
                )
            )

        if not triggered:
            return DoomLoopDecision(
                detected=False,
                classification="none",
                severity="none",
                action="none",
                reasons=[],
                metrics={
                    "repeated_tool_call": repeated_tool_call,
                    "repeated_failure": repeated_failure,
                    "no_progress": no_progress,
                    "oscillation": oscillation,
                },
                signal_count=len(recent),
                policy=normalized_policy,
            )

        triggered.sort(key=lambda item: (item[1], _CLASSIFICATION_PRIORITY.get(item[0], 0)), reverse=True)
        classification, ratio, primary_reason = triggered[0]
        severity = self._severity_from_ratio(ratio)
        action = normalized_policy["severity_actions"].get(severity, _DEFAULT_SEVERITY_ACTIONS["medium"])
        if repeated_failure >= int(normalized_policy["critical_abort_threshold"]):
            severity = "critical"
            action = "abort"

        reasons = [primary_reason] + [item[2] for item in triggered[1:]]
        return DoomLoopDecision(
            detected=True,
            classification=classification,
            severity=severity,
            action=action,
            reasons=reasons,
            metrics={
                "repeated_tool_call": repeated_tool_call,
                "repeated_failure": repeated_failure,
                "no_progress": no_progress,
                "oscillation": oscillation,
            },
            signal_count=len(recent),
            policy=normalized_policy,
        )

    @staticmethod
    def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def _normalize_signal(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        keys = {"task_id", "trace_id", "backend_name", "action_type", "failure_type", "iteration_count"}
        if not keys.issubset(set(value.keys())):
            return None
        return self.build_signal(
            task_id=value.get("task_id"),
            trace_id=value.get("trace_id"),
            backend_name=value.get("backend_name"),
            action_type=value.get("action_type"),
            failure_type=value.get("failure_type"),
            iteration_count=value.get("iteration_count"),
            action_signature=value.get("action_signature"),
            progress_made=value.get("progress_made"),
        )

    @staticmethod
    def _severity_from_ratio(ratio: float) -> str:
        if ratio >= 1.8:
            return "critical"
        if ratio >= 1.4:
            return "high"
        if ratio >= 1.0:
            return "medium"
        return "low"

    @staticmethod
    def _max_consecutive_failures(signals: list[dict[str, Any]]) -> int:
        current = 0
        maximum = 0
        for signal in signals:
            failure_type = str(signal.get("failure_type") or "success").strip().lower()
            if failure_type == "success":
                current = 0
                continue
            current += 1
            maximum = max(maximum, current)
        return maximum

    @staticmethod
    def _max_no_progress_streak(signals: list[dict[str, Any]]) -> int:
        current = 0
        maximum = 0
        for signal in signals:
            if bool(signal.get("progress_made")):
                current = 0
                continue
            current += 1
            maximum = max(maximum, current)
        return maximum

    @staticmethod
    def _max_repeated_tool_calls(signals: list[dict[str, Any]]) -> int:
        current = 0
        maximum = 0
        previous_signature: str | None = None
        for signal in signals:
            if str(signal.get("action_type") or "").strip().lower() != "tool_call":
                previous_signature = None
                current = 0
                continue
            signature = str(signal.get("action_signature") or "").strip().lower()
            if not signature:
                previous_signature = None
                current = 0
                continue
            if signature == previous_signature:
                current += 1
            else:
                previous_signature = signature
                current = 1
            maximum = max(maximum, current)
        return maximum

    @staticmethod
    def _max_oscillation_streak(signals: list[dict[str, Any]]) -> int:
        sequence = [
            str(signal.get("failure_type") or "").strip().lower()
            for signal in signals
            if str(signal.get("failure_type") or "").strip().lower() not in {"", "success"}
        ]
        if len(sequence) < 4:
            return 0
        best = 0
        for index in range(0, len(sequence) - 3):
            first = sequence[index]
            second = sequence[index + 1]
            if first == second:
                continue
            expected = first
            length = 2
            cursor = index + 2
            while cursor < len(sequence) and sequence[cursor] == expected:
                length += 1
                expected = second if expected == first else first
                cursor += 1
            best = max(best, length)
        return best


doom_loop_service = DoomLoopService()


def get_doom_loop_service() -> DoomLoopService:
    return doom_loop_service

"""State, LLM transport and evidence memory of the rag_iterative tool loop.

:mod:`agent.routes.snakes_rag_tool_loop` composes these collaborators into a
:class:`~agent.routes.snakes_rag_tool_loop_session.RagToolLoopSession`:

* :class:`RagToolLoopOptions` - immutable inputs of one loop run,
* :class:`RagToolLoopState` - mutable counters, messages and the trace,
* :class:`RagLlmTransport` - hub-worker profile routing or direct provider,
* :class:`EvidenceMemory` - files read so far and the rolling evidence prompt.
"""

from __future__ import annotations

import logging
import pathlib as _pl
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from agent.routes.snakes_rag_synthesis import build_synthesis_prompt
from agent.routes.snakes_rag_text_protocol import (
    compact_initial_packed_context,
    format_evidence_prompt,
    retire_initial_next_step_instruction,
)
from agent.services.snake_chat_cancellation import is_chat_cancelled

_log = logging.getLogger("agent.routes.snakes_rag_tool_loop")

_EVIDENCE_MESSAGE_PREFIX = "Recherche-Stand fuer die naechste LLM-Aktion:"
_CORRECTED_PATH_RE = re.compile(r"^\[Pfad automatisch korrigiert: .*? -> ([^\]]+)\]")


def budget_label(value: int) -> int | str:
    return value if value > 0 else "unlimited"


@dataclass(frozen=True)
class RagToolLoopOptions:
    """Immutable inputs of one tool-loop run (see ``run_rag_chat_tool_loop``)."""

    provider: str
    model: str | None
    repo_root: _pl.Path
    max_chars_per_file: int = 8000
    config_provider: Callable[[], dict[str, Any]] | None = None
    timeout: int = 180
    rec: Any | None = None
    initial_files: list[str] | None = None
    question: str = ""
    summarize_reads: bool = False
    max_summary_chars: int = 600
    initial_evidence: list[dict[str, Any]] | None = None
    architecture_context: str = ""
    cancel_event: Any | None = None
    final_task_kind: str = "repo_analysis"
    lock_tool_budgets: bool = False

    @property
    def model_label(self) -> str:
        return self.model or "auto"

    @property
    def final_synthesis_timeout(self) -> int:
        return min(360, max(300, self.timeout))


@dataclass
class RagToolLoopState:
    """Mutable state of one tool-loop run."""

    messages: list[dict]
    max_tool_calls: int
    max_search_calls: int
    trace: dict[str, Any]
    tool_call_count: int = 0
    llm_call_count: int = 0
    search_call_count: int = 0
    last_content: str = ""
    last_non_tool_content: str = ""
    force_final_next: bool = False
    final_repair_attempts: int = 0
    duplicate_call_streak: int = 0
    duplicate_calls_blocked: int = 0
    already_searched: set[str] = field(default_factory=set)
    codecompass_evidence: list[str] = field(default_factory=list)
    completed_codecompass_calls: set[str] = field(default_factory=set)

    @classmethod
    def start(cls, messages: list[dict], *, max_tool_calls: int, max_search_calls: int) -> "RagToolLoopState":
        return cls(
            messages=list(messages),
            max_tool_calls=max_tool_calls,
            max_search_calls=max_search_calls,
            trace={
                "mode": "tool_loop",
                "tool_calls_made": 0,
                "textual_tool_calls_detected": 0,
                "tools_used": [],
                "evidence": [],
                "max_tool_calls_effective": budget_label(max_tool_calls),
                "max_search_calls_effective": budget_label(max_search_calls),
            },
        )

    def tool_budget_left(self) -> bool:
        return self.max_tool_calls == 0 or self.tool_call_count < self.max_tool_calls

    def record_duplicate_blocked(self, *, streak: bool) -> None:
        self.duplicate_calls_blocked += 1
        if streak:
            self.duplicate_call_streak += 1
        self.trace["duplicate_calls_blocked"] = self.duplicate_calls_blocked

    def cancelled(self, cancel_event: Any) -> bool:
        if not is_chat_cancelled(cancel_event):
            return False
        self.trace["cancelled"] = True
        self.trace["error"] = "cancelled"
        return True


class RagLlmTransport:
    """Chat completions via hub-worker model profiles or a direct provider endpoint."""

    def __init__(
        self,
        *,
        use_profile_routing: bool,
        base_url: str,
        api_key: str | None,
        worker_profile_chat: Callable[..., tuple[Any, dict[str, Any]]],
        trace: dict[str, Any],
    ) -> None:
        self.use_profile_routing = use_profile_routing
        self.endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        self._worker_profile_chat = worker_profile_chat
        self._trace = trace

    def routed_chat(self, messages: list[dict], **kwargs) -> tuple[Any, dict[str, Any]]:
        """One worker-profile call; its routing trace is appended to ``worker_routes``."""
        data, routed_trace = self._worker_profile_chat(messages, **kwargs)
        self._trace.setdefault("worker_routes", []).append(routed_trace)
        return data, routed_trace

    def direct_chat(self, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        resp = requests.post(self.endpoint, json=payload, headers=self.headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()


def first_choice_content(response_data: dict[str, Any]) -> str:
    return str(((response_data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


class EvidenceMemory:
    """Files read so far, their compact summaries and the rolling evidence prompt."""

    def __init__(self, options: RagToolLoopOptions, state: RagToolLoopState, transport: RagLlmTransport) -> None:
        self._options = options
        self._state = state
        self._transport = transport
        self.files: dict[str, dict[str, Any]] = {}
        self.already_read: dict[str, str] = {}  # path → content, prevents re-reading the same file

    # ── file summaries ───────────────────────────────────────────────────────

    def summarize_file(self, path: str, content: str) -> str:
        """Intermediate LLM call: extract question-relevant info from a file into a compact summary."""
        options = self._options
        if self._state.cancelled(options.cancel_event):
            return "[Abgebrochen]"
        if not options.question or len(content) < 200:
            return content  # too short to bother summarizing
        summary_prompt = (
            f"Frage: {options.question[:300]}\n\n"
            f"Datei: {path}\n"
            # Cap input at 5000 chars to keep the summarization call fast
            f"```\n{content[:5000]}\n```\n\n"
            f"Extrahiere AUSSCHLIESSLICH die Informationen aus dieser Datei, die zur Frage direkt relevant sind. "
            f"Nenne konkrete Symbole, Funktionen, Klassen und Zeilenbezuege. "
            f"Maximal {options.max_summary_chars} Zeichen. "
            f"Falls nichts relevant: '[nicht relevant]'."
        )
        try:
            response_data = self._summary_completion(summary_prompt)
            if self._state.cancelled(options.cancel_event):
                return "[Abgebrochen]"
            summary = first_choice_content(response_data)
            if summary:
                return f"[Zusammenfassung von {path}]\n{summary[:options.max_summary_chars]}"
        except Exception as _exc:
            _log.warning("summarize_file failed for %s: %s", path, _exc)
        return content[:options.max_summary_chars]  # fallback: truncated raw content

    def _summary_completion(self, summary_prompt: str) -> dict[str, Any]:
        messages = [{"role": "user", "content": summary_prompt}]
        if self._transport.use_profile_routing:
            routed, routed_trace = self._transport.routed_chat(messages, task_kind="summarization")
            if not routed:
                raise RuntimeError(routed_trace.get("error") or "worker_profile_summary_failed")
            return routed
        return self._transport.direct_chat(
            {"model": self._options.model_label, "messages": messages},
            timeout=min(self._options.timeout, 120),
        )

    def summarize_traced(
        self,
        path: str,
        content: str,
        *,
        event_id: str,
        running_title: str,
        done_title: Callable[[int, int], str],
    ) -> str:
        """Summarize ``content`` and record running/completed trace events.

        ``done_title(raw_chars, summary_chars)`` builds the completion title.
        """
        rec = self._options.rec
        raw_chars = len(content)
        if rec:
            rec.event(event_id, running_title, status="running", details={"path": path, "raw_chars": raw_chars})
        summary = self.summarize_file(path, content)
        if rec:
            rec.event(
                event_id,
                done_title(raw_chars, len(summary)),
                status="completed",
                details={"path": path, "raw_chars": raw_chars, "summary_chars": len(summary)},
                output_preview=summary,
            )
        return summary

    # ── evidence bookkeeping ─────────────────────────────────────────────────

    def _publish(self) -> None:
        self._state.trace["evidence"] = list(self.files.values())

    def remember_file(self, path: str, content: str, *, source: str, score: Any = None) -> None:
        max_chars = self._options.max_summary_chars
        compact = content.strip()
        if len(compact) > max_chars:
            compact = compact[:max_chars] + f"\n... [Evidence gekuerzt nach {max_chars} Zeichen]"
        self.files[path] = {"path": path, "summary": compact, "score": score, "source": source, "chars": len(content)}
        self._publish()

    def cache_read_result(self, requested_path: str, result: str, *, source: str) -> None:
        self.already_read[requested_path] = result
        remembered_path = requested_path
        corrected = _CORRECTED_PATH_RE.match(result)
        if corrected:
            remembered_path = corrected.group(1).strip()
            self.already_read[remembered_path] = result
        self.remember_file(remembered_path, result, source=source)

    def register_initial_evidence(self) -> None:
        options = self._options
        for idx, item in enumerate(options.initial_evidence or [], 1):
            if self._state.cancelled(options.cancel_event):
                return
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            content = str(item.get("content") or "").strip()
            summary = str(item.get("summary") or "").strip() or "Datei wurde im Initialkontext bereitgestellt."
            if options.summarize_reads and content:
                summary = self.summarize_traced(
                    path,
                    content,
                    event_id=f"initial_context_{idx}_summarize",
                    running_title=f"Initialkontext zusammenfassen: {path}",
                    done_title=lambda _raw, _summary, path=path: f"Initialkontext zusammengefasst: {path}",
                )
            self.files[path] = {
                "path": path,
                "summary": summary,
                "score": item.get("score"),
                "source": item.get("source") or "initial_context",
                "chars": item.get("chars") or len(content),
            }
            self.already_read[path] = f"[Datei '{path}' ist bereits im Initialkontext enthalten.]\n{summary}"
        self._publish()

    # ── conversation messages ────────────────────────────────────────────────

    def prompt(self) -> str:
        return format_evidence_prompt(self.files, self._options.question)

    def compact_initial_packed_context(self) -> None:
        if self.files and compact_initial_packed_context(self._state.messages):
            self._state.trace["initial_context_compacted_for_followups"] = True

    def replace_or_append_message(self, evidence_text: str) -> None:
        if not evidence_text:
            return
        messages = self._state.messages
        messages[:] = [
            msg for msg in messages
            if not (msg.get("role") == "user" and str(msg.get("content") or "").startswith(_EVIDENCE_MESSAGE_PREFIX))
        ]
        messages.append({"role": "user", "content": evidence_text})

    def retire_initial_next_step_instruction(self) -> None:
        if retire_initial_next_step_instruction(self._state.messages):
            self._state.trace["initial_next_step_instruction_retired"] = True

    def prepare_final_synthesis_context(self, *, retry: bool = False) -> None:
        synthesis_prompt = build_synthesis_prompt(
            question=self._options.question,
            architecture_context=self._options.architecture_context,
            codecompass_evidence=self._state.codecompass_evidence,
            evidence=self.prompt(),
            research_hint=self._state.last_non_tool_content,
            retry=retry,
        )
        self._state.messages[:] = [{"role": "user", "content": synthesis_prompt}]
        self._state.trace["final_synthesis_context_chars"] = len(synthesis_prompt)
        self._state.trace["final_synthesis_context_mode"] = "retry_compact" if retry else "bounded"

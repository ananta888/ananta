"""ChatPromptBuilder — consolidated prompt construction with explicit context budget policy.

Budget policy order: active_target → rolling_summary → recent_turns → codecompass → rag
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.chat_memory import ChatMemoryContext


@dataclass(frozen=True)
class PromptBuildResult:
    messages: list[dict[str, str]]        # OpenAI-style for LMStudio/local
    prompt_text: str                       # Plain text for propose/worker
    worker_v2_payload: dict[str, Any]     # Structured for /snake/ask v2
    included_sections: dict[str, int]     # section name → char count
    total_chars: int


class ChatPromptBuilder:
    def __init__(
        self,
        *,
        question: str,
        depth: str,
        memory: ChatMemoryContext,
        context_budget: int = 3000,
        max_turns_chars: int = 1800,
        system_template: str = "",
    ) -> None:
        self._question = str(question or "").strip()
        self._depth = str(depth or "overview")
        self._memory = memory
        self._budget = max(200, context_budget)
        self._max_turns_chars = max(100, max_turns_chars)
        self._system_template = system_template

    def build(self) -> PromptBuildResult:
        sections: dict[str, str] = {}
        remaining = self._budget

        if self._memory.active_target_excerpt and remaining > 100:
            s = self._memory.active_target_excerpt[:remaining]
            sections["active_target"] = s
            remaining -= len(s)

        if self._memory.rolling_summary and remaining > 50:
            s = self._memory.rolling_summary[:min(remaining, 1500)]
            sections["rolling_summary"] = s
            remaining -= len(s)

        if self._memory.recent_turns and remaining > 50:
            turns_text = self._format_turns()
            s = turns_text[:min(remaining, self._max_turns_chars)]
            sections["recent_turns"] = s
            remaining -= len(s)

        if self._memory.codecompass_refs and remaining > 50:
            joined = "\n".join(self._memory.codecompass_refs)[:remaining]
            sections["codecompass"] = joined
            remaining -= len(joined)

        if self._memory.rag_snippets and remaining > 50:
            joined = "\n".join(self._memory.rag_snippets)[:remaining]
            sections["rag"] = joined
            remaining -= len(joined)

        if self._memory.runtime_status and remaining > 20:
            sections["runtime_status"] = self._memory.runtime_status[:remaining]

        context_text = self._assemble_context(sections)
        depth_instruction = _depth_instruction(self._depth)
        project_name = Path.cwd().name

        system_content = self._build_system(context_text, depth_instruction, project_name)
        messages = [{"role": "system", "content": system_content}]
        prior = self._memory.to_prior_messages()
        char_budget = self._budget // 2
        used = 0
        trimmed: list[dict[str, str]] = []
        for msg in reversed(prior):
            chunk = str(msg.get("content") or "")
            if used + len(chunk) > char_budget and trimmed:
                break
            trimmed.insert(0, msg)
            used += len(chunk)
        messages.extend(trimmed)
        messages.append({"role": "user", "content": self._question})

        prompt_text = self._build_plain_prompt(context_text, depth_instruction)

        worker_v2 = {
            "question": self._question,
            "context": context_text,
            "depth": self._depth,
            "memory_context": {
                "recent_turns": [{"role": t.role, "content": t.content} for t in self._memory.recent_turns],
                "rolling_summary": self._memory.rolling_summary,
                "codecompass_refs": self._memory.codecompass_refs[:8],
                "metadata": {"memory_version": "v2", **self._memory.metadata},
            },
        }

        included = {k: len(v) for k, v in sections.items()}
        return PromptBuildResult(
            messages=messages,
            prompt_text=prompt_text,
            worker_v2_payload=worker_v2,
            included_sections=included,
            total_chars=sum(included.values()),
        )

    def _format_turns(self) -> str:
        parts: list[str] = []
        for t in self._memory.recent_turns:
            label = "User" if t.role == "user" else "Assistant"
            parts.append(f"{label}: {t.content}")
        return "\n".join(parts)

    def _assemble_context(self, sections: dict[str, str]) -> str:
        order = ["active_target", "rolling_summary", "recent_turns", "codecompass", "rag", "runtime_status"]
        parts: list[str] = []
        for key in order:
            if key in sections and sections[key]:
                if key == "rolling_summary":
                    parts.append(f"[Gesprächshistorie]\n{sections[key]}")
                elif key == "recent_turns":
                    parts.append(f"[Letzte Nachrichten]\n{sections[key]}")
                elif key == "codecompass":
                    parts.append(f"[Codekontext]\n{sections[key]}")
                elif key == "rag":
                    parts.append(f"[Weitere Referenzen]\n{sections[key]}")
                else:
                    parts.append(sections[key])
        return "\n\n".join(parts)

    def _build_system(self, context_text: str, depth_instruction: str, project_name: str) -> str:
        if self._system_template and "{context}" in self._system_template:
            return (
                self._system_template
                .replace("{context}", context_text[:self._budget])
                .replace("{depth_instruction}", depth_instruction)
                .replace("{project_name}", project_name)
            )
        return (
            f"Du bist ein hilfreicher Assistent für das Projekt {project_name}.\n"
            f"Kontext:\n{context_text[:self._budget]}\n{depth_instruction}"
        )

    def _build_plain_prompt(self, context_text: str, depth_instruction: str) -> str:
        parts = [f"Depth: {self._depth}", depth_instruction]
        if self._memory.rolling_summary:
            parts.append(f"[Gesprächshistorie]\n{self._memory.rolling_summary}")
        if self._memory.recent_turns:
            parts.append(f"[Letzte Nachrichten]\n{self._format_turns()}")
        if context_text:
            parts.append(f"[Kontext]\n{context_text[:2000]}")
        parts.append(f"Frage: {self._question}")
        return "\n\n".join(parts)


def _depth_instruction(depth: str) -> str:
    return {
        "overview": "Antworte in 2-3 Sätzen.",
        "deep": "Antworte in 3-4 Sätzen mit einem konkreten Beispiel oder Codepfad.",
        "expert": "Antworte technisch präzise mit Dateipfaden oder API-Referenzen wenn möglich.",
    }.get(depth, "Antworte präzise und hilfreich.")

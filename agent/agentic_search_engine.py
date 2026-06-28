from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent.repository_map_engine import ContextChunk


@dataclass(slots=True)
class SearchSkill:
    name: str
    priority: int
    trigger: Callable[[str], bool]
    build_command: Callable[[str], list[str]]


class AgenticSearchEngine:
    """Vibe-like skill registry with deterministic planning and execution budgets."""

    METACHAR_PATTERN = re.compile(r"[;&|`><$(){}]")

    def __init__(
        self,
        repo_root: str | Path,
        max_output_chars: int = 5000,
        max_commands: int = 3,
        command_timeout_seconds: int = 8,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_output_chars = max_output_chars
        self.max_commands = max_commands
        self.command_timeout_seconds = command_timeout_seconds
        self.allowed_commands = {"rg", "ls", "cat"}
        self.skills = [
            SearchSkill(
                name="file_discovery",
                priority=1,
                trigger=lambda q: any(w in q.lower() for w in ("where", "datei", "file", "struktur", "tree")),
                build_command=lambda _q: ["rg", "--files"],
            ),
            SearchSkill(
                name="config_probe",
                priority=2,
                trigger=lambda q: any(w in q.lower() for w in ("config", "env", "setting", "einstellung")),
                build_command=lambda _q: [
                    "rg",
                    "--files",
                    "-g",
                    "*.env",
                    "-g",
                    "*.json",
                    "-g",
                    "*.yaml",
                    "-g",
                    "*.yml",
                ],
            ),
            SearchSkill(
                name="text_grep",
                priority=3,
                trigger=lambda _q: True,
                build_command=lambda q: ["rg", "-n", "--no-heading", "--max-count", "40", self._sanitize_query(q), "."],
            ),
        ]

    @classmethod
    def _sanitize_query(cls, query: str) -> str:
        cleaned = re.sub(r"[\r\n\t]+", " ", query).strip()
        if cls.METACHAR_PATTERN.search(cleaned):
            cleaned = cls.METACHAR_PATTERN.sub(" ", cleaned)
        return cleaned[:180]

    def _is_allowed_command(self, args: list[str]) -> bool:
        if not args or args[0] not in self.allowed_commands:
            return False
        return all("\n" not in arg and "\r" not in arg for arg in args)

    def _run(self, args: list[str]) -> str:
        if not self._is_allowed_command(args):
            return ""
        try:
            completed = subprocess.run(  # noqa: S603 - command is sanitized/allowlisted before execution
                args,
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
                shell=False,
            )
        except Exception as e:
            logging.debug(f"Agentic command failed: {' '.join(args)} ({e})")
            return ""
        output = completed.stdout.strip() or completed.stderr.strip()
        return output[: self.max_output_chars]

    def _plan(self, query: str) -> list[SearchSkill]:
        matches = [skill for skill in self.skills if skill.trigger(query)]
        matches.sort(key=lambda skill: skill.priority)
        return matches[: self.max_commands]

    def _apply_scope(self, args: list[str], allowed_paths: list[str]) -> list[str] | None:
        """CCRDS-010: rewrite a planned command to search only allowed paths.

        ``rg`` gets the scoped paths as explicit positional targets (an
        existing trailing ``.`` is replaced); ``cat``/``ls`` targets must
        already lie inside the scope, otherwise the command is dropped.
        Query content can never widen the scope: the query is a single
        sanitized pattern argument, the paths are appended afterwards.
        """
        from agent.codecompass.domain_scope import is_path_within, normalize_repo_relative_path

        if not args:
            return None
        if args[0] == "rg":
            scoped = args[:-1] if args[-1] == "." else list(args)
            return scoped + sorted(allowed_paths)
        # cat/ls: every path argument must be inside the scope.
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            normalized = normalize_repo_relative_path(arg, repo_root=self.repo_root)
            if normalized is None or not is_path_within(normalized, allowed_paths):
                return None
        return list(args)

    def search(
        self,
        query: str,
        top_k: int = 3,
        allowed_paths: list[str] | None = None,
    ) -> list[ContextChunk]:
        if allowed_paths is not None and not allowed_paths:
            # Active scope without any allowed path: never search globally.
            return []
        planned = self._plan(query)
        max_steps = min(len(planned), self.max_commands, max(top_k, 1))
        chunks: list[ContextChunk] = []
        used_output = 0
        for skill in planned[:max_steps]:
            args = skill.build_command(query)
            if allowed_paths is not None:
                args = self._apply_scope(args, allowed_paths)
                if args is None:
                    continue
            out = self._run(args)
            if not out:
                continue
            remaining = self.max_output_chars - used_output
            if remaining <= 0:
                break
            out = out[:remaining]
            used_output += len(out)
            chunks.append(
                ContextChunk(
                    engine="agentic_search",
                    source=" ".join(args),
                    content=out,
                    score=1.0 + min(len(out) / 5000.0, 1.0),
                    metadata={"skill": skill.name},
                )
            )
        return chunks[:top_k]

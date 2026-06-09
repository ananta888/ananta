"""Eval-Runner fuer Base-vs-Adapter Vergleich (MLLORA-015).

- Eval-Dataset gegen Base und Base+Adapter laufen lassen (oder Dry-Run)
- Fuer ananta-todo-json: JSON-Validitaet, Pflichtfelder, Milestones, Tasks, ACs prufen
- Adapter mit schlechterem Score als Base wird nicht approved
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EvalError(ValueError):
    """Fehler im Eval-Runner."""


# --- Scoring ---------------------------------------------------------------

def _score_todo_json(output_text: str) -> dict[str, Any]:
    """Deterministisches Scoring fuer ananta-todo-json Ausgaben."""
    text = output_text.strip()
    scores: dict[str, bool] = {}

    # JSON-Validitaet
    parsed = None
    try:
        parsed = json.loads(text)
        scores["json_valid"] = True
    except (json.JSONDecodeError, ValueError):
        # Evtl. JSON innerhalb eines Textes
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                scores["json_valid"] = True
            except (json.JSONDecodeError, ValueError):
                pass
        if "json_valid" not in scores:
            scores["json_valid"] = False

    if parsed and isinstance(parsed, dict):
        # Pflichtfelder Track-Ebene
        scores["has_track"] = bool(parsed.get("track"))
        scores["has_milestones"] = isinstance(parsed.get("milestones"), list) and len(parsed["milestones"]) > 0
        scores["has_tasks"] = isinstance(parsed.get("tasks"), list) and len(parsed["tasks"]) > 0

        # Task-Qualitaet
        tasks = parsed.get("tasks") or []
        task_quality: list[bool] = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            has_ac = isinstance(t.get("acceptance_criteria"), list) and len(t["acceptance_criteria"]) > 0
            has_te = isinstance(t.get("test_expectations"), list) and len(t["test_expectations"]) > 0
            has_required = all(t.get(k) for k in ("id", "title", "status", "priority", "risk"))
            task_quality.append(has_ac and has_te and has_required)
        scores["task_quality_ratio"] = (sum(task_quality) / len(task_quality)) if task_quality else 0.0
    else:
        scores["has_track"] = False
        scores["has_milestones"] = False
        scores["has_tasks"] = False
        scores["task_quality_ratio"] = 0.0

    total = (
        int(scores["json_valid"]) * 3 +
        int(scores.get("has_track", False)) * 1 +
        int(scores.get("has_milestones", False)) * 1 +
        int(scores.get("has_tasks", False)) * 1 +
        float(scores.get("task_quality_ratio", 0.0)) * 4
    ) / 10.0  # 0.0 .. 1.0

    return {**scores, "total": round(total, 3)}


def _score_generic(output_text: str) -> dict[str, Any]:
    """Einfaches generisches Scoring: Laenge und Nicht-Leer."""
    text = output_text.strip()
    return {
        "non_empty": bool(text),
        "length": len(text),
        "total": 1.0 if text else 0.0,
    }


_SCORERS = {
    "todo_json": _score_todo_json,
    "ananta_todo_json": _score_todo_json,
}


# --- Eval-Ergebnis -Strukturen ---------------------------------------------

@dataclass
class EvalSample:
    prompt_index: int
    prompt: str
    base_output: str
    adapter_output: str
    base_score: dict[str, Any]
    adapter_score: dict[str, Any]
    adapter_wins: bool


@dataclass
class EvalReport:
    eval_id: str
    adapter_id: str | None
    base_model: str
    eval_dataset_path: str
    eval_dataset_hash: str
    adapter_path: str | None
    scorer_name: str
    sample_count: int
    base_avg_score: float
    adapter_avg_score: float
    adapter_better_than_base: bool
    samples: list[EvalSample] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema"] = "mlintern_eval_report.v1"
        return d


# --- Eval-Service ----------------------------------------------------------

class MlInternLoraEvalService:
    """Eval-Runner fuer Base vs. Adapter Vergleich."""

    def evaluate(
        self,
        *,
        base_model: str,
        eval_dataset_path: str | Path,
        adapter_path: str | Path | None = None,
        adapter_id: str | None = None,
        scorer_name: str = "generic",
        base_output_fn: Any = None,
        adapter_output_fn: Any = None,
        dry_run: bool = False,
    ) -> EvalReport:
        """Fuehrt Eval aus.

        Args:
            base_output_fn: Callable(prompt) -> str fuer Basismodell-Output (None = Dummy in dry_run)
            adapter_output_fn: Callable(prompt) -> str fuer Adapter-Output (None = Dummy in dry_run)
        """
        path = Path(eval_dataset_path)
        if not path.exists():
            raise EvalError(f"eval_dataset not found: {path}")

        eval_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        dataset_hash = self._hash_file(path)
        scorer = _SCORERS.get(scorer_name, _score_generic)

        samples: list[EvalSample] = []
        errors: list[str] = []

        records = self._load_dataset(path, errors)
        for i, record in enumerate(records):
            prompt = self._extract_prompt(record)
            if not prompt:
                errors.append(f"record {i}: could not extract prompt")
                continue

            if dry_run or base_output_fn is None:
                base_out = f"[dry_run base output for: {prompt[:80]}]"
            else:
                try:
                    base_out = str(base_output_fn(prompt) or "")
                except Exception as exc:
                    base_out = ""
                    errors.append(f"record {i}: base_output_fn failed: {exc}")

            if dry_run or adapter_output_fn is None:
                adapter_out = f"[dry_run adapter output for: {prompt[:80]}]"
            else:
                try:
                    adapter_out = str(adapter_output_fn(prompt) or "")
                except Exception as exc:
                    adapter_out = ""
                    errors.append(f"record {i}: adapter_output_fn failed: {exc}")

            base_score = scorer(base_out)
            adapter_score = scorer(adapter_out)
            samples.append(EvalSample(
                prompt_index=i,
                prompt=prompt[:500],
                base_output=base_out[:1000],
                adapter_output=adapter_out[:1000],
                base_score=base_score,
                adapter_score=adapter_score,
                adapter_wins=float(adapter_score.get("total", 0)) >= float(base_score.get("total", 0)),
            ))

        base_avg = (sum(float(s.base_score.get("total", 0)) for s in samples) / len(samples)) if samples else 0.0
        adapter_avg = (sum(float(s.adapter_score.get("total", 0)) for s in samples) / len(samples)) if samples else 0.0
        adapter_better = adapter_avg >= base_avg

        return EvalReport(
            eval_id=eval_id,
            adapter_id=adapter_id,
            base_model=base_model,
            eval_dataset_path=str(path),
            eval_dataset_hash=dataset_hash,
            adapter_path=str(adapter_path) if adapter_path else None,
            scorer_name=scorer_name,
            sample_count=len(samples),
            base_avg_score=round(base_avg, 4),
            adapter_avg_score=round(adapter_avg, 4),
            adapter_better_than_base=adapter_better,
            samples=samples,
            errors=errors,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def write_report(self, report: EvalReport, output_path: str | Path) -> None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    @staticmethod
    def _hash_file(path: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _load_dataset(path: Path, errors: list[str]) -> list[dict]:
        records = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    errors.append(f"line {i}: JSON parse error: {exc}")
        return records

    @staticmethod
    def _extract_prompt(record: dict) -> str:
        if "instruction" in record:
            inst = str(record.get("instruction") or "")
            inp = str(record.get("input") or "")
            return f"{inst}\n{inp}".strip() if inp else inst
        if "messages" in record:
            msgs = record.get("messages") or []
            user_msgs = [str(m.get("content") or "") for m in msgs if isinstance(m, dict) and m.get("role") == "user"]
            return " ".join(user_msgs)[:1000]
        return ""


_eval_instance: MlInternLoraEvalService | None = None


def get_lora_eval_service() -> MlInternLoraEvalService:
    global _eval_instance
    if _eval_instance is None:
        _eval_instance = MlInternLoraEvalService()
    return _eval_instance

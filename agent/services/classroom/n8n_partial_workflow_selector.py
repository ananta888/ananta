"""CTA-007: n8n-Teilworkflow-Selektor.

Bevorzugt vorhandene funktionierende Beispiele (kind n8n_workflow des
fertigen n8n-Tracks; Startbestand rag-helper/tests/fixtures/n8n/) vor
jeder LLM-Generierung. Credentials erscheinen im Output ausschliesslich
als {credential_type}-Platzhalter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

FORM_SINGLE_NODE = "single_node"
FORM_SUBFLOW = "subflow"
FORM_FULL_WORKFLOW = "full_workflow"

IMPORT_HINT_IMPORTABLE = "importable"
IMPORT_HINT_MANUAL = "manual_insert"

_TERM_TO_ROLE_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webhook", ("webhook",)),
    ("http", ("httprequest",)),
    ("api", ("httprequest",)),
    ("trigger", ("trigger", "webhook", "cron", "schedule")),
    ("merge", ("merge",)),
    ("wait", (".wait",)),
    ("if", (".if",)),
    ("switch", ("switch",)),
    ("llm", ("openai", "openrouter", "lmchat", "agent")),
    ("openai", ("openai", "lmchat")),
    ("agent", ("agent",)),
    ("credential", ()),
    ("subworkflow", ("executeworkflow",)),
)

_FULL_WORKFLOW_REQUEST = re.compile(r"komplett|ganzer workflow|beispiel.?workflow|gesamten", re.IGNORECASE)


def load_fixture_workflows(examples_dir: str | Path) -> list[dict]:
    """Laedt funktionierende Beispiel-Workflows (Default: die Fixtures
    des n8n-Tracks). Nicht-Workflow-JSON wird uebersprungen."""
    workflows: list[dict] = []
    directory = Path(examples_dir)
    if not directory.is_dir():
        return workflows
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("nodes"), list) and isinstance(item.get("connections"), dict):
                workflows.append({"file": str(path), "name": str(item.get("name") or path.stem), "workflow": item})
    return workflows


def _redact_credentials(node: dict) -> dict:
    cleaned = dict(node)
    credentials = cleaned.get("credentials")
    if isinstance(credentials, dict):
        cleaned["credentials"] = {str(ctype): "{" + str(ctype) + "}" for ctype in credentials.keys()}
    return cleaned


def _matching_roles(question: str) -> set[str]:
    lowered = str(question or "").lower()
    needles: set[str] = set()
    for term, role_needles in _TERM_TO_ROLE_NEEDLES:
        if term in lowered:
            needles.update(role_needles)
    return needles


class N8nPartialWorkflowSelector:
    def __init__(
        self,
        examples_dir: str | Path = "rag-helper/tests/fixtures/n8n",
        generator: Callable[[str], dict] | None = None,
        workflows_provider: Callable[[], list[dict]] | None = None,
    ) -> None:
        self.examples_dir = examples_dir
        self.generator = generator
        self.workflows_provider = workflows_provider

    def select(self, *, question_text: str) -> dict | None:
        """Liefert einen Vorschlag {form, part, import_hint, source_ref,
        origin} oder None, wenn die Frage nicht n8n-bezogen ist."""
        needles = _matching_roles(question_text)
        workflows = self.workflows_provider() if self.workflows_provider else load_fixture_workflows(self.examples_dir)

        best: tuple[int, dict, list[dict]] | None = None
        for entry in workflows:
            nodes = [n for n in entry["workflow"].get("nodes") or [] if isinstance(n, dict)]
            matched = [n for n in nodes if any(needle in str(n.get("type") or "").lower() for needle in needles)]
            if matched and (best is None or len(matched) > best[0]):
                best = (len(matched), entry, matched)

        if best is None:
            if self.generator is None or not needles:
                return None
            # Letzter Ausweg: Generierung — MUSS durch den Verifier
            # (CTA-008); der Aufrufer erzwingt das.
            generated = self.generator(question_text)
            return {
                "form": FORM_FULL_WORKFLOW,
                "part": generated,
                "import_hint": IMPORT_HINT_MANUAL,
                "source_ref": None,
                "origin": "generated",
            }

        _, entry, matched = best
        workflow = entry["workflow"]
        source_ref = {"file": entry["file"], "workflow_name": entry["name"]}

        if _FULL_WORKFLOW_REQUEST.search(str(question_text or "")):
            cleaned = dict(workflow)
            cleaned["nodes"] = [_redact_credentials(n) for n in workflow.get("nodes") or []]
            cleaned.pop("pinData", None)
            return {"form": FORM_FULL_WORKFLOW, "part": cleaned, "import_hint": IMPORT_HINT_IMPORTABLE, "source_ref": source_ref, "origin": "fixture"}

        if len(matched) == 1 and not self._neighbours(workflow, matched[0]):
            return {
                "form": FORM_SINGLE_NODE,
                "part": _redact_credentials(matched[0]),
                "import_hint": IMPORT_HINT_MANUAL,
                "source_ref": source_ref,
                "origin": "fixture",
            }

        included = self._with_neighbours(workflow, matched)
        included_names = {str(n.get("name")) for n in included}
        connections = {
            source: {
                group: [
                    [t for t in targets if isinstance(t, dict) and str(t.get("node")) in included_names]
                    for targets in outputs
                ]
                for group, outputs in groups.items()
                if isinstance(outputs, list)
            }
            for source, groups in (workflow.get("connections") or {}).items()
            if source in included_names and isinstance(groups, dict)
        }
        return {
            "form": FORM_SUBFLOW,
            "part": {"nodes": [_redact_credentials(n) for n in included], "connections": connections},
            "import_hint": IMPORT_HINT_MANUAL,
            "source_ref": source_ref,
            "origin": "fixture",
        }

    # ── intern ───────────────────────────────────────────────────────────

    @staticmethod
    def _neighbours(workflow: dict, node: dict) -> list[str]:
        name = str(node.get("name"))
        neighbours: list[str] = []
        for source, groups in (workflow.get("connections") or {}).items():
            if not isinstance(groups, dict):
                continue
            for outputs in groups.values():
                if not isinstance(outputs, list):
                    continue
                for targets in outputs:
                    for target in targets if isinstance(targets, list) else []:
                        if not isinstance(target, dict):
                            continue
                        if source == name:
                            neighbours.append(str(target.get("node")))
                        elif str(target.get("node")) == name:
                            neighbours.append(str(source))
        return neighbours

    def _with_neighbours(self, workflow: dict, matched: list[dict]) -> list[dict]:
        nodes = [n for n in workflow.get("nodes") or [] if isinstance(n, dict)]
        wanted = {str(n.get("name")) for n in matched}
        for node in matched:
            wanted.update(self._neighbours(workflow, node))
        return [n for n in nodes if str(n.get("name")) in wanted]

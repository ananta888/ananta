"""Teaching material extractor (CTA-004, track classroom-transcript-codecompass-assistant).

Indexiert Unterrichtsmaterialien (Markdown) als eigene Record-Familie —
nach dem erprobten n8n-Extractor-Muster: opt-in per
teaching_extractor_cls, Frontmatter-Konvention als Metadaten-Träger,
from/to/type-Relations, keine 'source'-Felder (_CONTEXT_DROP_KEYS).

Frontmatter-Konvention (YAML zwischen ---, geparst mit
ObsidianExtractor.parse_frontmatter):

    module_id: M04
    task_id: M04-A2            # optional; ohne task_id ist die Datei Modul-Doku
    material_kind: task        # module | task | hint | solution
    day: tue                   # optional
    schedule_slot: "09:00-10:30"  # optional
    related_n8n_workflows: ["Webhook Order Routing"]

Ohne Frontmatter: Heading-Heuristik (H1=Modul, H2=Aufgaben) mit
confidence='low'. Kein neuer n8n-Kind: Verweise zielen auf den
bestehenden kind n8n_workflow; Aufloesung passiert per
link_material_workflow_relations, wenn Material und n8n-Fixtures im
selben Lauf indexiert werden.
"""
from __future__ import annotations

from rag_helper.extractors.obsidian import parse_frontmatter
from rag_helper.utils.embedding_text import build_embedding_text, compact_text
from rag_helper.utils.ids import safe_id

_KIND_BY_MATERIAL = {
    "module": "teaching_module",
    "task": "teaching_task",
    "hint": "teaching_hint",
    "solution": "known_solution",
}

RELATION_TASK_BELONGS_TO_MODULE = "teaching_task_belongs_to_module"
RELATION_HINT_FOR_TASK = "hint_for_task"
RELATION_SOLUTION_FOR_TASK = "solution_for_task"
RELATION_TASK_USES_N8N_WORKFLOW = "task_uses_n8n_workflow"


class TeachingMaterialExtractor:
    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        frontmatter, body = parse_frontmatter(text)
        if frontmatter.get("module_id") or frontmatter.get("material_kind"):
            return self._parse_with_frontmatter(rel_path, frontmatter, body)
        return self._parse_heuristic(rel_path, text)

    # ── frontmatter path ─────────────────────────────────────────────────

    def _parse_with_frontmatter(self, rel_path: str, frontmatter: dict, body: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        module_id = str(frontmatter.get("module_id") or "unknown-module").strip()
        task_id = str(frontmatter.get("task_id") or "").strip() or None
        material_kind = str(frontmatter.get("material_kind") or ("task" if task_id else "module")).strip().lower()
        kind = _KIND_BY_MATERIAL.get(material_kind, "teaching_module")

        primary_id = self._record_id(kind, rel_path, module_id, task_id or "")
        module_record_id = f"teaching_module:{safe_id(rel_path, module_id)}"
        title = self._first_heading(body) or task_id or module_id
        summary_text = compact_text(body, 300)

        index_records: list[dict] = []
        relations: list[dict] = []

        primary = {
            "kind": kind,
            "file": rel_path,
            "id": primary_id,
            "name": title,
            "module_id": module_id,
            "task_id": task_id,
            "material_kind": material_kind,
            "day": frontmatter.get("day"),
            "schedule_slot": frontmatter.get("schedule_slot"),
            "confidence": "high",
            "summary": f"{material_kind} '{title}' (Modul {module_id}{', Aufgabe ' + task_id if task_id else ''})",
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"Teaching {material_kind} {title} in module {module_id}"
                    f"{' task ' + task_id if task_id else ''} ({rel_path}). "
                    f"Content: {summary_text}"
                ),
                f"{material_kind} {title} ({module_id}{'/' + task_id if task_id else ''}): {compact_text(body, 100)}",
            ),
        }
        if kind != "teaching_module":
            primary["parent_id"] = module_record_id
        index_records.append(primary)

        detail = {
            "kind": "teaching_material_detail",
            "file": rel_path,
            "id": f"teaching_material_detail:{safe_id(rel_path, module_id, task_id or '', material_kind)}",
            "parent_id": primary_id,
            "module_id": module_id,
            "task_id": task_id,
            "content": compact_text(body, 2000),
        }

        if kind == "teaching_task":
            relations.append({"from": primary_id, "to": module_record_id, "type": RELATION_TASK_BELONGS_TO_MODULE})
        elif kind in ("teaching_hint", "known_solution") and task_id:
            # Die Task liegt in einer ANDEREN Datei — direkte from/to-Kante
            # wuerde danglen (passthrough prueft Existenz nicht). Daher
            # make_relation-Format: Graph-Export verwirft Unaufloesbares,
            # link_material_workflow_relations loest projektweit auf.
            relation_type = RELATION_HINT_FOR_TASK if kind == "teaching_hint" else RELATION_SOLUTION_FOR_TASK
            relations.append({
                "kind": "relation",
                "file": rel_path,
                "id": f"relation:{safe_id(rel_path, primary_id, relation_type, module_id, task_id)}",
                "source_id": primary_id,
                "source_kind": kind,
                "relation": relation_type,
                "target": f"{module_id}/{task_id}",
            })

        related = frontmatter.get("related_n8n_workflows") or []
        if isinstance(related, str):
            related = [related]
        for workflow_name in related:
            name = str(workflow_name or "").strip()
            if not name:
                continue
            # Cross-File-Ziel: im make_relation-Format ausgeben; die
            # Graph-Symbolaufloesung verwirft es (kein dangling), und
            # link_material_workflow_relations loest es projektweit auf,
            # wenn der n8n-Workflow im selben Lauf indexiert wurde.
            relations.append({
                "kind": "relation",
                "file": rel_path,
                "id": f"relation:{safe_id(rel_path, primary_id, RELATION_TASK_USES_N8N_WORKFLOW, name)}",
                "source_id": primary_id,
                "source_kind": kind,
                "relation": RELATION_TASK_USES_N8N_WORKFLOW,
                "target": name,
            })

        stats = {
            "kind": "teaching_material",
            "file": rel_path,
            "material_kind": material_kind,
            "module_id": module_id,
            "task_id": task_id,
            "record_count": len(index_records) + 1,
            "confidence": "high",
        }
        return index_records, [detail], relations, stats

    # ── heuristic path (no frontmatter) ──────────────────────────────────

    def _parse_heuristic(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        lines = text.splitlines()
        module_title = None
        tasks: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and module_title is None:
                module_title = stripped[2:].strip()
            elif stripped.startswith("## "):
                tasks.append(stripped[3:].strip())
        module_title = module_title or rel_path

        module_id = f"teaching_module:{safe_id(rel_path, module_title)}"
        index_records: list[dict] = [{
            "kind": "teaching_module",
            "file": rel_path,
            "id": module_id,
            "name": module_title,
            "module_id": None,
            "task_id": None,
            "material_kind": "module",
            "confidence": "low",
            "summary": f"module '{module_title}' (heuristisch, kein Frontmatter)",
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"Teaching module {module_title} ({rel_path}), extracted heuristically without frontmatter. Tasks: {', '.join(tasks[:8]) or 'none'}.",
                f"module {module_title}: {', '.join(tasks[:4]) or 'no tasks'}",
            ),
        }]
        relations: list[dict] = []
        for task_title in tasks:
            task_record_id = f"teaching_task:{safe_id(rel_path, module_title, task_title)}"
            index_records.append({
                "kind": "teaching_task",
                "file": rel_path,
                "id": task_record_id,
                "parent_id": module_id,
                "name": task_title,
                "module_id": None,
                "task_id": None,
                "material_kind": "task",
                "confidence": "low",
                "summary": f"task '{task_title}' in module '{module_title}' (heuristisch)",
                "embedding_text": build_embedding_text(
                    self.embedding_text_mode,
                    f"Teaching task {task_title} in module {module_title} ({rel_path}), heuristic extraction.",
                    f"task {task_title} ({module_title})",
                ),
            })
            relations.append({"from": task_record_id, "to": module_id, "type": RELATION_TASK_BELONGS_TO_MODULE})

        stats = {
            "kind": "teaching_material",
            "file": rel_path,
            "material_kind": "module",
            "record_count": len(index_records),
            "confidence": "low",
        }
        return index_records, [], relations, stats

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _record_id(kind: str, rel_path: str, module_id: str, task_id: str) -> str:
        return f"{kind}:{safe_id(rel_path, module_id, task_id)}"

    @staticmethod
    def _first_heading(body: str) -> str | None:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return None


def link_material_workflow_relations(index_records: list[dict], relation_records: list[dict]) -> int:
    """Projektweiter Post-Schritt: loest task_uses_n8n_workflow-,
    hint_for_task- und solution_for_task-Verweise in direkte from/to-
    Kanten auf, wenn das Ziel im selben Lauf indexiert wurde.

    Rueckgabe: Anzahl aufgeloester Relations. Unaufloesbare bleiben im
    make_relation-Format und werden vom Graph-Export verworfen (kein
    dangling). Wird in process_project nach der Aggregation aufgerufen,
    wenn teaching_extractor_cls aktiv ist.
    """
    workflows_by_name = {
        str(record.get("name") or ""): str(record.get("id"))
        for record in index_records
        if record.get("kind") == "n8n_workflow" and record.get("name")
    }
    tasks_by_module_task = {
        (str(record.get("module_id") or ""), str(record.get("task_id") or "")): str(record.get("id"))
        for record in index_records
        if record.get("kind") == "teaching_task" and record.get("task_id")
    }

    resolved = 0
    for relation in relation_records:
        if "to" in relation:
            continue
        relation_type = str(relation.get("relation") or "")
        target_id: str | None = None
        if relation_type == RELATION_TASK_USES_N8N_WORKFLOW:
            target_id = workflows_by_name.get(str(relation.get("target") or ""))
        elif relation_type in (RELATION_HINT_FOR_TASK, RELATION_SOLUTION_FOR_TASK):
            raw_target = str(relation.get("target") or "")
            module_id, _, task_id = raw_target.partition("/")
            target_id = tasks_by_module_task.get((module_id, task_id))
        if target_id:
            relation["from"] = relation.pop("source_id")
            relation["to"] = target_id
            relation["type"] = relation_type
            relation.pop("relation", None)
            relation.pop("target", None)
            resolved += 1
    return resolved

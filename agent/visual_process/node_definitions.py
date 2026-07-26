"""Composable UI definitions derived from the runtime task-kind registry.

Runtime truth stays in :mod:`task_kind_registry`; this module contributes only
presentation, form and graph-contract metadata.  This split lets new renderers
extend the editor without copying execution or policy flags.
"""

from __future__ import annotations

import copy
from typing import Any

from agent.visual_process.step_executor import get_step_executor
from agent.visual_process.task_kind_registry import canonical_task_kind_ids, list_task_kinds

NODE_DEFINITION_CONTRACT_VERSION = "ananta.visual_process.node_definition.v1"
NODE_REGISTRY_VERSION = "1.0.0"


def _field(
    path: str,
    label: str,
    field_type: str,
    *,
    help_text: str,
    default: Any = None,
    required: bool = False,
    constraints: dict[str, Any] | None = None,
    options: list[dict[str, Any]] | None = None,
    resource_type: str | None = None,
    example: Any = None,
    effect: str | None = None,
    essential: bool = True,
    visible_when: dict[str, Any] | None = None,
    required_when: dict[str, Any] | None = None,
    deprecated: bool = False,
    read_only: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path,
        "label": label,
        "field_type": field_type,
        "help_text": help_text,
        "required": required,
        "essential": essential,
        "deprecated": deprecated,
        "read_only": read_only,
    }
    if default is not None:
        result["default"] = default
    if constraints:
        result["constraints"] = constraints
    if options:
        result["options"] = options
    if resource_type:
        result["resource_type"] = resource_type
    if example is not None:
        result["example"] = example
    if effect:
        result["effect"] = effect
    if visible_when:
        result["visible_when"] = visible_when
    if required_when:
        result["required_when"] = required_when
    return result


_COMMON_FIELDS = [
    _field(
        "/label",
        "Label",
        "text",
        help_text="Sichtbarer Schrittname.",
        required=True,
        example="Änderung prüfen",
        effect="Identifiziert den Schritt in Canvas, Review und Laufstatus.",
    ),
    _field("/role", "Rolle", "resource_reference", help_text="Optionale Worker-Rolle.", resource_type="worker_role"),
    _field(
        "/agent_skill_profile_id",
        "Skill-Profil",
        "resource_reference",
        help_text="Hub-verwaltetes Skill-Profil.",
        resource_type="skill_profile",
    ),
    _field("/gate", "Freigabe", "boolean", help_text="Explizite Nutzerfreigabe vor Ausführung.", default=False),
    _field(
        "/policy_hints",
        "Policy-Hinweise",
        "multi_select",
        help_text="Nicht-autoritative Hinweise; der Hub entscheidet die effektive Policy.",
        default=[],
        options=[
            {"value": "read_only", "label": "Read only"},
            {"value": "requires_approval", "label": "Freigabe erforderlich"},
            {"value": "high_risk", "label": "Hohes Risiko"},
        ],
    ),
    _field("/io/inputs", "Inputs", "io_port", help_text="Typisierte Eingangsartefakte.", default=[]),
    _field("/io/outputs", "Outputs", "io_port", help_text="Typisierte Ausgangsartefakte.", default=[]),
    _field(
        "/metadata/description",
        "Beschreibung",
        "text",
        help_text="Fachliche Erklärung dieses Schritts.",
        example="Prüft den vorgeschlagenen Patch gegen die Akzeptanzkriterien.",
        effect="Wird als begrenzter Arbeitskontext an den ausführenden Worker übergeben.",
    ),
]


_QUERY_FIELDS = [
    _field(
        "/metadata/query",
        "Query",
        "text",
        help_text="Begrenzte Suchanfrage.",
        example="Wo wird metadata.weight gelesen?",
        effect="Steuert die Retrieval-Abfrage; ein Upstream-query-Artefakt kann den Wert ersetzen.",
    ),
    _field(
        "/metadata/top_k",
        "Top K",
        "number",
        help_text="Maximale Trefferzahl.",
        default=20,
        constraints={"minimum": 1, "maximum": 200, "integer": True},
    ),
]

_MODEL_ROUTING_FIELDS = [
    _field(
        "/metadata/model_routing/preferred_profile_id",
        "Bevorzugtes Modellprofil",
        "resource_reference",
        help_text="Hub-autorisiertes Modellprofil für diesen LLM-Schritt.",
        resource_type="model_profile",
        essential=False,
        effect="Beeinflusst die Hub-seitige Modellauswahl, ohne Worker-Policy zu überschreiben.",
    ),
    _field(
        "/metadata/model_routing/fallback_group_id",
        "Fallback-Gruppe",
        "resource_reference",
        help_text="Hub-verwaltete geordnete Fallback-Gruppe.",
        resource_type="fallback_group",
        essential=False,
    ),
    _field(
        "/metadata/model_routing/allow_cloud",
        "Cloud erlauben",
        "boolean",
        help_text="Erlaubt Cloud-Kandidaten nur innerhalb der Hub-Policy.",
        essential=False,
    ),
    _field(
        "/metadata/model_routing/context_recovery_strategies",
        "Kontext-Notfallkette",
        "multi_select",
        help_text=(
            "Geordnete Hub-Strategien nach erschöpfter Modellkette; Worker "
            "dürfen daraus keine Tasks selbst erzeugen."
        ),
        options=[
            {"value": "compact_context", "label": "Kontext verdichten"},
            {"value": "segment_planning", "label": "In Abschnitte planen"},
            {"value": "propose_task_plan", "label": "Task-Plan vorschlagen"},
            {"value": "require_approval", "label": "Freigabe anfordern"},
            {"value": "stop", "label": "Danach stoppen"},
        ],
        essential=False,
        effect=(
            "Der Hub darf nach terminaler Modell-Erschöpfung einen begrenzten "
            "Planentwurf erzeugen; die Reihenfolge wird als Policy transportiert."
        ),
    ),
    _field(
        "/metadata/model_routing/require_approval_for_generated_plan",
        "Generierten Task-Plan freigeben",
        "boolean",
        help_text=(
            "Bindet die Task-Materialisierung an eine exakte, Hub-seitige "
            "Freigabe des gespeicherten Plan-Digests."
        ),
        essential=False,
    ),
]

_OPENAI_PROVIDER = {"path": "/metadata/provider", "equals_any": ["openai", "openai_compatible"]}


def _port(name: str, kind: str, *, required: bool = True, description: str = "") -> dict[str, Any]:
    return {"name": name, "kind": kind, "required": required, "description": description}


_KIND_PORTS: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
    "query_rewrite": ([_port("query", "text")], [_port("rewritten_query", "json")]),
    "rag_retrieve": ([_port("query", "text")], [_port("candidates", "json")]),
    "rerank": ([_port("query", "text"), _port("candidates", "json")], [_port("reranked", "json")]),
    "embed_api": ([_port("texts", "json")], [_port("embeddings", "vector")]),
    "embed_chunk": ([_port("documents", "json")], [_port("chunks", "json"), _port("embeddings", "vector")]),
    "sign_rotation": ([_port("vector", "vector")], [_port("rotated", "vector")]),
    "turboquant_mse": ([_port("vector", "vector")], [_port("quantized", "vector")]),
    "workspace_snapshot": ([], [_port("snapshot", "json"), _port("artifact_manifest", "json")]),
    "workspace_diff": (
        [_port("before_snapshot", "json"), _port("after_snapshot", "json")],
        [_port("diff", "json"), _port("artifact_manifest", "json")],
    ),
    "codecompass_index_build": ([_port("repository_snapshot", "json")], [_port("index_manifest", "json")]),
    "codecompass_vector_search": ([_port("query", "text")], [_port("evidence", "json")]),
    "codecompass_fts_search": ([_port("query", "text")], [_port("evidence", "json")]),
    "codecompass_graph_expand": ([_port("seed_refs", "json")], [_port("evidence", "json")]),
    "ml_intern_build_lora_dataset": ([_port("records", "dataset", required=False)], [_port("dataset_id", "dataset")]),
    "ml_intern_train_lora": ([_port("dataset_id", "dataset")], [_port("job_result", "json")]),
}


_KIND_FIELDS: dict[str, list[dict[str, Any]]] = {
    "patch_apply": [
        _field(
            "/metadata/patch_artifact_ref",
            "Patch",
            "resource_reference",
            help_text="Geprüftes Patch-Artefakt.",
            resource_type="artifact",
        ),
    ],
    "patch_propose": [
        _field("/metadata/instructions", "Anweisung", "text", help_text="Begrenzte Patch-Anweisung."),
        _field("/metadata/allowed_tools", "Tools", "structured_list", help_text="Hub-autorisierte Tools.", default=[]),
    ],
    "command_execute": [
        _field(
            "/metadata/command",
            "Befehl",
            "text",
            help_text="Policy-geprüfter Befehl.",
            required=True,
            example="pytest -q",
            effect="Wird ausschließlich über den delegierten Worker und dessen Tool-Policy ausgeführt.",
        )
    ],
    "shell_execute": [
        _field(
            "/metadata/command",
            "Shell-Befehl",
            "text",
            help_text="Policy-geprüfter Shell-Befehl.",
            required=True,
            example="git status --short",
            effect="Erzeugt einen Worker-Task mit Shell-Risiko und bleibt Hub-governed.",
        )
    ],
    "plan_only": [
        _field(
            "/metadata/instructions",
            "Planungsauftrag",
            "text",
            help_text="Begrenzter Auftrag für eine reine Planung.",
            required=True,
        )
    ],
    "script": [
        _field(
            "/metadata/script_ref",
            "Skript",
            "resource_reference",
            help_text="Workspace-relatives Skript.",
            resource_type="workspace_file",
        )
    ],
    "file_check": [
        _field(
            "/metadata/path",
            "Pfad",
            "resource_reference",
            help_text="Workspace-relative Datei.",
            resource_type="workspace_file",
        )
    ],
    "regex_check": [
        _field(
            "/metadata/path",
            "Pfad",
            "resource_reference",
            help_text="Workspace-relative Datei.",
            resource_type="workspace_file",
        ),
        _field("/metadata/pattern", "Muster", "expression", help_text="Begrenzter regulärer Ausdruck."),
    ],
    "git_op": [
        _field(
            "/metadata/operation",
            "Git-Operation",
            "enum",
            help_text="Erlaubte lesende Git-Operation.",
            default="status",
            options=[{"value": value, "label": value} for value in ("status", "diff", "log")],
        ),
    ],
    "run_tests": [
        _field(
            "/metadata/test_command",
            "Testbefehl",
            "text",
            help_text="Policy-geprüfter Testbefehl.",
            example="pytest -q tests/test_example.py",
        ),
    ],
    "review": [
        _field(
            "/metadata/review_focus",
            "Prüffokus",
            "text",
            help_text="Fachlicher Prüffokus.",
            example="Security und Rückwärtskompatibilität",
        )
    ],
    "summarize": [
        _field("/metadata/instructions", "Zusammenfassung", "text", help_text="Gewünschte Zusammenfassungsform.")
    ],
    "research_limited": [
        _field("/metadata/question", "Frage", "text", help_text="Begrenzte Recherchefrage.", required=True)
    ],
    "query_rewrite": [copy.deepcopy(_QUERY_FIELDS[0])],
    "rerank": [
        _field("/metadata/query", "Query", "text", help_text="Query für den Token-Overlap-Vergleich."),
        _field(
            "/metadata/weight",
            "Gewicht",
            "number",
            help_text="Kanonisches Backend-Rerankergewicht.",
            default=0.15,
            constraints={"minimum": 0, "maximum": 1},
        ),
        _field(
            "/metadata/enabled",
            "Aktiv",
            "boolean",
            help_text="Aktiviert den deterministischen Token-Overlap-Boost.",
            default=True,
        ),
    ],
    "embed_api": [
        _field(
            "/metadata/provider",
            "Provider",
            "enum",
            help_text="Registrierter Embedding-Provider.",
            default="hash",
            options=[
                {"value": "hash", "label": "Lokaler Hash"},
                {"value": "openai_compatible", "label": "OpenAI-kompatibel"},
            ],
            effect="Hash bleibt lokal; OpenAI-kompatibel benötigt expliziten Netzwerk-Opt-in.",
        ),
        _field(
            "/metadata/base_url",
            "Basis-URL",
            "text",
            help_text="Policy-geprüfter OpenAI-kompatibler Endpunkt.",
            example="http://embedding.local/v1",
            visible_when=_OPENAI_PROVIDER,
            required_when=_OPENAI_PROVIDER,
        ),
        _field(
            "/metadata/model",
            "Modell",
            "resource_reference",
            help_text="Freigegebenes Embedding-Modell.",
            resource_type="model_profile",
            visible_when=_OPENAI_PROVIDER,
            required_when=_OPENAI_PROVIDER,
        ),
        _field(
            "/metadata/dimensions",
            "Dimensionen",
            "number",
            help_text="Ausgabedimensionen des Providers.",
            default=12,
            constraints={"minimum": 1, "maximum": 65536, "integer": True},
        ),
        _field(
            "/metadata/external_calls_allowed",
            "Externe Aufrufe erlauben",
            "boolean",
            help_text="Expliziter, weiterhin Hub-policy-gebundener Netzwerk-Opt-in.",
            default=False,
            visible_when=_OPENAI_PROVIDER,
            required_when=_OPENAI_PROVIDER,
        ),
        _field(
            "/metadata/api_key_secret_ref",
            "API-Key-Referenz",
            "secret_reference",
            help_text="Opake env://-Secret-Referenz; niemals Klartext.",
            example="env://EMBEDDING_API_KEY",
            visible_when=_OPENAI_PROVIDER,
            required_when=_OPENAI_PROVIDER,
        ),
    ],
    "embed_chunk": [
        _field(
            "/metadata/chunk_size",
            "Chunk-Größe",
            "number",
            help_text="Maximale Chunk-Größe.",
            default=800,
            constraints={"minimum": 1, "maximum": 8000, "integer": True},
        ),
        _field(
            "/metadata/chunk_overlap",
            "Überlappung",
            "number",
            help_text="Chunk-Überlappung.",
            default=100,
            constraints={"minimum": 0, "maximum": 2000, "integer": True},
        ),
    ],
    "codecompass_vector_search": copy.deepcopy(_QUERY_FIELDS),
    "codecompass_fts_search": copy.deepcopy(_QUERY_FIELDS),
    "codecompass_graph_expand": [
        _field(
            "/metadata/seed_refs",
            "Seeds",
            "structured_list",
            help_text="Autoritative Source-/Symbol-Referenzen.",
            default=[],
        ),
        _field(
            "/metadata/max_nodes",
            "Maximale Knoten",
            "number",
            help_text="Harte Expansionsgrenze.",
            default=50,
            constraints={"minimum": 1, "maximum": 500, "integer": True},
        ),
    ],
    "codecompass_index_build": [
        _field(
            "/metadata/snapshot_ref",
            "Snapshot",
            "resource_reference",
            help_text="Hub-autorisierter Repository-Snapshot.",
            resource_type="repository_snapshot",
        ),
        _field(
            "/metadata/incremental",
            "Inkrementell",
            "boolean",
            help_text="Nur geänderte Quellen verarbeiten.",
            default=True,
        ),
    ],
    "workspace_snapshot": [
        _field(
            "/metadata/workspace_root",
            "Workspace",
            "resource_reference",
            help_text="Hub-validierter relativer Workspace-Root.",
            default=".",
            resource_type="workspace_directory",
        ),
    ],
    "workspace_diff": [
        _field(
            "/metadata/workspace_root",
            "Workspace",
            "resource_reference",
            help_text="Hub-validierter relativer Workspace-Root.",
            default=".",
            resource_type="workspace_directory",
        ),
    ],
    "ml_intern_build_lora_dataset": [
        _field(
            "/metadata/dataset_id",
            "Dataset",
            "resource_reference",
            help_text="Hub-katalogisiertes Dataset.",
            resource_type="training_dataset",
        ),
        _field(
            "/metadata/name",
            "Dataset-Name",
            "text",
            help_text="Name für ein aus Upstream-Records katalogisiertes Dataset.",
            example="Kuratierte Supportdialoge",
        ),
        _field(
            "/metadata/format",
            "Format",
            "enum",
            help_text="Kanonisches Recordformat.",
            default="instruction",
            options=[{"value": value, "label": value} for value in ("instruction", "chat", "completion")],
        ),
        _field(
            "/metadata/validation_ratio",
            "Validierungsanteil",
            "number",
            help_text="Reproduzierbarer Validation-Split.",
            default=0.1,
            constraints={"minimum": 0.05, "maximum": 0.5},
        ),
        _field(
            "/metadata/split_seed",
            "Split-Seed",
            "number",
            help_text="Deterministischer Split-Seed.",
            default=42,
            constraints={"minimum": 0, "maximum": 2147483647, "integer": True},
        ),
        _field("/metadata/purpose", "Zweck", "text", help_text="Dokumentierter Trainingszweck.", essential=False),
        _field("/metadata/license", "Lizenz", "text", help_text="Lizenz- oder Nutzungsinformation.", essential=False),
        _field(
            "/metadata/privacy",
            "Datenschutz",
            "enum",
            help_text="Katalogisierte Datenschutzklasse.",
            default="private",
            options=[{"value": value, "label": value} for value in ("private", "internal", "public")],
            essential=False,
        ),
    ],
    "ml_intern_train_lora": [
        _field(
            "/metadata/dataset_id",
            "Dataset",
            "resource_reference",
            help_text="Validiertes Hub-Dataset.",
            resource_type="training_dataset",
            required=True,
        ),
        _field(
            "/metadata/base_model",
            "Basismodell",
            "resource_reference",
            help_text="Freigegebenes lokales Basismodell.",
            resource_type="model_profile",
            required=True,
        ),
        _field(
            "/metadata/training_profile_id",
            "Trainingsprofil",
            "resource_reference",
            help_text="Hub-verwaltetes Trainingsprofil.",
            resource_type="training_profile",
            required=True,
        ),
        _field(
            "/metadata/mode",
            "Ausführungsmodus",
            "enum",
            help_text="Dry-run oder explizit bestätigter Live-Lauf.",
            default="dry_run",
            options=[{"value": value, "label": value} for value in ("dry_run", "live")],
        ),
        _field(
            "/metadata/backend",
            "Backend",
            "enum",
            help_text="Hub-erlaubtes Trainingsbackend.",
            default="mock",
            options=[{"value": value, "label": value} for value in ("mock", "transformers", "unsloth")],
        ),
        _field(
            "/metadata/method",
            "Adapterverfahren",
            "enum",
            help_text="LoRA- oder 4-bit-QLoRA-Verfahren.",
            default="qlora",
            options=[{"value": "lora", "label": "LoRA"}, {"value": "qlora", "label": "QLoRA"}],
        ),
        _field(
            "/metadata/output_name",
            "Adaptername",
            "text",
            help_text="Kanonischer Name des erzeugten Adapterartefakts.",
            default="vp-lora-adapter",
            constraints={"pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
        ),
        _field(
            "/metadata/max_steps",
            "Maximale Schritte",
            "number",
            help_text="Hub-validierte Trainingsobergrenze.",
            constraints={"minimum": 1, "maximum": 100000, "integer": True},
            essential=False,
        ),
        _field(
            "/metadata/lora_rank",
            "LoRA-Rang",
            "number",
            help_text="Rang der Adaptermatrizen.",
            default=8,
            constraints={"minimum": 1, "maximum": 256, "integer": True},
            essential=False,
        ),
        _field(
            "/metadata/require_dataset_validation",
            "Dataset validieren",
            "boolean",
            help_text="Start nur nach bestandener Dataset-Validierung.",
            default=True,
        ),
        _field(
            "/metadata/require_secret_scan",
            "Secret-Scan",
            "boolean",
            help_text="Start nur nach bestandenem Secret-Scan.",
            default=True,
        ),
    ],
    "sign_rotation": [
        _field(
            "/metadata/seed",
            "Seed",
            "number",
            help_text="Deterministischer Vorzeichenrotations-Seed.",
            default=888,
            constraints={"minimum": 0, "maximum": 2147483647, "integer": True},
        ),
    ],
    "turboquant_mse": [
        _field(
            "/metadata/seed",
            "Seed",
            "number",
            help_text="Deterministischer Quantisierungs-Seed.",
            default=888,
            constraints={"minimum": 0, "maximum": 2147483647, "integer": True},
        ),
        _field(
            "/metadata/levels",
            "Stufen",
            "number",
            help_text="Experimentelle skalare Quantisierungsstufen.",
            default=7,
            constraints={"minimum": 2, "maximum": 256, "integer": True},
        ),
    ],
    "rag_retrieve": copy.deepcopy(_QUERY_FIELDS)
    + [
        _field(
            "/metadata/channels",
            "Kanäle",
            "multi_select",
            help_text="Begrenzte Retrieval-Kanäle.",
            default=["dense", "lexical"],
            options=[
                {"value": value, "label": value}
                for value in (
                    "dense",
                    "lexical",
                    "symbol",
                    "codecompass_fts",
                    "codecompass_vector",
                    "codecompass_graph",
                )
            ],
        ),
    ],
    "domain_cluster": [
        _field(
            "/metadata/min_domain_size",
            "Minimale Domain-Größe",
            "number",
            help_text="Mindestzahl zusammengehöriger Elemente.",
            default=2,
            constraints={"minimum": 1, "maximum": 100000, "integer": True},
        ),
    ],
    "evolution_analyze": [
        _field(
            "/metadata/provider_name",
            "Provider",
            "resource_reference",
            help_text="Hub-registrierter Evolutionsprovider.",
            resource_type="evolution_provider",
        ),
        _field(
            "/metadata/trigger_type",
            "Trigger",
            "enum",
            help_text="Expliziter Auslöser der Analyse.",
            default="manual",
            options=[
                {"value": value, "label": value} for value in ("manual", "verification_failure", "quality_regression")
            ],
        ),
        _field(
            "/metadata/analyze_only", "Nur analysieren", "boolean", help_text="Verhindert Mutationen.", default=True
        ),
    ],
    "evolution_validate": [
        _field(
            "/metadata/provider_name",
            "Provider",
            "resource_reference",
            help_text="Hub-registrierter Evolutionsprovider.",
            resource_type="evolution_provider",
        ),
        _field(
            "/metadata/proposal_id",
            "Proposal",
            "resource_reference",
            help_text="Persistierter Evolution-Vorschlag.",
            resource_type="evolution_proposal",
            required=True,
        ),
    ],
    "evolution_apply": [
        _field(
            "/metadata/provider_name",
            "Provider",
            "resource_reference",
            help_text="Hub-registrierter Evolutionsprovider.",
            resource_type="evolution_provider",
        ),
        _field(
            "/metadata/proposal_id",
            "Proposal",
            "resource_reference",
            help_text="Validierter Evolution-Vorschlag.",
            resource_type="evolution_proposal",
            required=True,
        ),
        _field(
            "/metadata/apply_allowed",
            "Anwendung anfordern",
            "boolean",
            help_text="Erfordert zusätzlich MutationGate, Hub-Policy und Nutzerfreigabe.",
            default=False,
            effect="Fordert eine schreibende Operation an, autorisiert sie aber nicht.",
        ),
    ],
    "evolve_prompt": [
        _field(
            "/metadata/prompt_template_ref",
            "Prompt-Template",
            "resource_reference",
            help_text="Versioniertes Hub-Prompt-Template.",
            resource_type="prompt_template",
            required=True,
        ),
        _field(
            "/metadata/target_model_family",
            "Modellfamilie",
            "text",
            help_text="Zielmodellfamilie für die Optimierung.",
            essential=False,
        ),
    ],
    "evolve_project": [
        _field(
            "/metadata/project_ref",
            "Projekt-Snapshot",
            "resource_reference",
            help_text="Hub-autorisierter Projekt-Snapshot.",
            resource_type="repository_snapshot",
            required=True,
        ),
        _field(
            "/metadata/apply_allowed",
            "Änderungen anfordern",
            "boolean",
            help_text="Erfordert zusätzlich MutationGate, Hub-Policy und Nutzerfreigabe.",
            default=False,
            effect="Fordert die schreibende Phase an, autorisiert sie aber nicht.",
        ),
    ],
    "fork": [
        _field(
            "/metadata/branch_labels",
            "Zweige",
            "structured_list",
            help_text="Deterministisch sortierte Zweigbezeichner.",
            default=[],
        )
    ],
    "approval": [
        _field("/metadata/approval_message", "Freigabetext", "text", help_text="Angezeigte Freigabebegründung.")
    ],
}


def _defaults(fields: list[dict[str, Any]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for item in fields:
        path = str(item["path"])
        if path.startswith("/metadata/") and "default" in item:
            target = metadata
            segments = [
                segment.replace("~1", "/").replace("~0", "~") for segment in path.removeprefix("/metadata/").split("/")
            ]
            for segment in segments[:-1]:
                nested = target.get(segment)
                if not isinstance(nested, dict):
                    nested = {}
                    target[segment] = nested
                target = nested
            target[segments[-1]] = copy.deepcopy(item["default"])
    return {"metadata": metadata}


def compose_node_definition(info: dict[str, Any]) -> dict[str, Any]:
    kind = str(info["id"])
    mode = get_step_executor().execution_mode(kind)
    fields = copy.deepcopy(_COMMON_FIELDS) + copy.deepcopy(_KIND_FIELDS.get(kind, []))
    if bool(info["uses_llm"]):
        fields.extend(copy.deepcopy(_MODEL_ROUTING_FIELDS))
    paths = [str(item["path"]) for item in fields]
    if len(paths) != len(set(paths)):
        raise RuntimeError(f"node_definition_field_path_drift:{kind}")
    runtime = {
        key: copy.deepcopy(info[key])
        for key in (
            "implementation_status",
            "implementation_state",
            "backend_service",
            "deterministic",
            "uses_llm",
            "uses_network",
            "side_effects",
            "risk_level",
            "legacy_aliases",
            "requires_approval",
            "dispatch_capable",
        )
    }
    executable = mode != "not_executable"
    runtime_claims_executable = str(info["implementation_state"]) == "wired_and_executable"
    if runtime_claims_executable != executable:
        raise RuntimeError(f"node_definition_runtime_execution_drift:{kind}:{info['implementation_state']}:{mode}")
    inputs, outputs = copy.deepcopy(_KIND_PORTS.get(kind, ([], [])))
    return {
        "contract_version": NODE_DEFINITION_CONTRACT_VERSION,
        "registry_version": NODE_REGISTRY_VERSION,
        "kind": kind,
        "label": info["label"],
        "category": info["group"],
        "purpose": info["description"],
        "runtime": runtime,
        "execution": {
            "execution_mode": mode,
            "visual_process_executable": executable,
            "worker_dispatch_capable": bool(info["dispatch_capable"]),
            "adapter_available": mode == "vp_adapter",
        },
        "inputs": inputs,
        "outputs": outputs,
        "fields": fields,
        "defaults": _defaults(fields),
        "help_text": info["description"],
        "examples": [{"label": info["label"], "kind": kind}],
        "capabilities": {
            "supports_model_routing": bool(info["uses_llm"]),
            "requires_approval": bool(info["requires_approval"]),
            "has_side_effects": bool(info["side_effects"]),
        },
    }


def list_node_definitions() -> list[dict[str, Any]]:
    definitions = [compose_node_definition(dict(item)) for item in list_task_kinds()]
    ids = {item["kind"] for item in definitions}
    if ids != set(canonical_task_kind_ids()):
        raise RuntimeError("node_definition_registry_drift")
    return definitions


def get_node_definition(kind: str) -> dict[str, Any] | None:
    return next((item for item in list_node_definitions() if item["kind"] == kind), None)


def allowed_step_patch_paths(kind: str) -> frozenset[str]:
    definition = get_node_definition(kind)
    if definition is None:
        return frozenset()
    return frozenset(
        str(item["path"])
        for item in definition["fields"]
        if not bool(item.get("read_only")) and not bool(item.get("deprecated"))
    )


__all__ = [
    "NODE_DEFINITION_CONTRACT_VERSION",
    "NODE_REGISTRY_VERSION",
    "allowed_step_patch_paths",
    "compose_node_definition",
    "get_node_definition",
    "list_node_definitions",
]

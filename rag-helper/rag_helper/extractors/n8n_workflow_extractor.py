"""n8n workflow extractor (track codecompass-n8n-workflow-understanding).

Parses exported n8n workflow JSON files into CodeCompass records:

* index records: kind=n8n_workflow, n8n_node, n8n_credential_ref,
  n8n_http_endpoint
* detail records: kind=n8n_node_detail (redacted parameter context)
* relation records in the direct ``{"from", "to", "type"}`` edge format
  that ``build_graph_edges`` passes through unchanged; optional edge
  attributes ``field``/``http_method``/``endpoint_path`` are copied onto
  the edges automatically.

Security contract: credential values, secret-looking parameter values,
query-string tokens and pinData payloads are never emitted. Credentials
appear only as name/type references.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rag_helper.extractors.base import FileSkipped
from rag_helper.utils.embedding_text import build_embedding_text, compact_list, compact_text
from rag_helper.utils.ids import safe_id

_SECRET_KEY_PATTERN = re.compile(
    r"(token|api[-_]?key|authorization|passwor[dt]|secret|bearer|credential|private[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S{8,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|rk|xoxb|xoxp|ghp|gho|glpat)[-_][A-Za-z0-9_\-]{10,}"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),
)
_REDACTED = "<redacted>"

_EXPRESSION_PATTERN = re.compile(
    r"\$json(?:\.[A-Za-z_][\w]*|\[\s*[\"'][^\"'\]]+[\"']\s*\])+"
    r"|\$node\[\s*[\"'][^\"'\]]+[\"']\s*\]"
    r"|\$env\.[A-Za-z_][\w]*"
    r"|\$workflow\.[A-Za-z_][\w]*"
    r"|\$items?\(\s*[\"'][^\"')]*[\"']\s*\)",
)

_MAX_EXPRESSION_REFS = 20
_MAX_PARAMETER_SUMMARY_KEYS = 20
_MAX_PARAMETER_VALUE_CHARS = 160

# role label heuristics keyed on the lowercased node type
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webhook", ("webhook",)),
    ("trigger", ("trigger", "webhook", "cron", "schedule")),
    ("api_call", ("httprequest", "graphql")),
    ("llm", ("openai", "openrouter", "anthropic", "lmchat", "chainllm", "langchain.agent")),
    ("branch", (".if", "switch")),
    ("merge", ("merge",)),
    ("wait", (".wait",)),
    ("code", (".code", "function")),
    ("subworkflow", ("executeworkflow",)),
    ("email", ("emailsend", "gmail", "emailreadimap")),
)


def is_n8n_workflow(payload: object) -> bool:
    """True when the payload looks like a single exported n8n workflow."""
    if not isinstance(payload, dict):
        return False
    nodes = payload.get("nodes")
    connections = payload.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        return False
    if not nodes:
        return True
    dictish = [node for node in nodes if isinstance(node, dict)]
    if not dictish:
        return False
    typed = [node for node in dictish if isinstance(node.get("type"), str) and node.get("name")]
    return len(typed) >= max(1, len(dictish) // 2)


def _looks_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def _normalize_url(raw: str) -> str:
    """Strip secret-looking query parameters, keep host and path."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.query:
        return raw
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (_SECRET_KEY_PATTERN.search(key) or _looks_secret_value(value))
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _role_labels_for_type(node_type: str) -> list[str]:
    lowered = node_type.lower()
    labels = [role for role, needles in _ROLE_RULES if any(needle in lowered for needle in needles)]
    return sorted(set(labels))


class N8nWorkflowExtractor:
    """StructuredExtractor for exported n8n workflow JSON files."""

    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict], dict]:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise FileSkipped("invalid_json", {"error": str(exc)[:200]})

        if isinstance(payload, list):
            workflows = [item for item in payload if is_n8n_workflow(item)]
        elif is_n8n_workflow(payload):
            workflows = [payload]
        else:
            workflows = []
        if not workflows:
            raise FileSkipped("not_n8n_workflow")

        index_records: list[dict] = []
        detail_records: list[dict] = []
        connect_relations: list[dict] = []
        api_relations: list[dict] = []
        field_relations: list[dict] = []
        totals = {
            "workflow_count": 0,
            "node_count": 0,
            "connection_count": 0,
            "trigger_count": 0,
            "credential_ref_count": 0,
            "redacted_value_count": 0,
        }
        findings_totals: dict[str, int] = {}
        workflow_ids_by_name: dict[str, str] = {}
        pending_subworkflow_refs: list[tuple[str, str]] = []

        for workflow in workflows:
            state = _WorkflowState(self, rel_path, workflow)
            state.build()
            index_records.extend(state.index_records)
            detail_records.extend(state.detail_records)
            connect_relations.extend(state.connect_relations)
            api_relations.extend(state.api_relations)
            field_relations.extend(state.field_relations)
            pending_subworkflow_refs.extend(state.subworkflow_refs)
            workflow_ids_by_name[state.workflow_name] = state.workflow_id
            for key in ("workflow_count", "node_count", "connection_count", "trigger_count", "credential_ref_count", "redacted_value_count"):
                totals[key] += state.totals[key]
            for name, count in state.findings.items():
                findings_totals[name] = findings_totals.get(name, 0) + count

        # Subworkflow references: resolve within this file when the target
        # workflow exists locally, otherwise emit the make_relation format so
        # the graph symbol resolution deliberately drops it instead of
        # creating a dangling edge.
        for source_node_id, target_name in pending_subworkflow_refs:
            target_workflow_id = workflow_ids_by_name.get(target_name)
            if target_workflow_id:
                api_relations.append({"from": source_node_id, "to": target_workflow_id, "type": "n8n_invokes_subworkflow"})
            else:
                api_relations.append({
                    "kind": "relation",
                    "file": rel_path,
                    "id": f"relation:{safe_id(rel_path, source_node_id, 'n8n_invokes_subworkflow', target_name)}",
                    "source_id": source_node_id,
                    "source_kind": "n8n_node",
                    "relation": "n8n_invokes_subworkflow",
                    "target": target_name,
                })

        # Priority order matters: compact_relation_records truncates from the
        # end, so connections survive before API relations before data fields.
        relation_records = [*connect_relations, *api_relations, *field_relations]
        for relation in relation_records:
            relation.setdefault("file", rel_path)
        stats = {
            "kind": "n8n",
            "file": rel_path,
            **totals,
            "security_findings": findings_totals,
        }
        return index_records, detail_records, relation_records, stats


class _WorkflowState:
    """Record builder for a single workflow within one export file."""

    def __init__(self, extractor: N8nWorkflowExtractor, rel_path: str, workflow: dict) -> None:
        self.extractor = extractor
        self.rel_path = rel_path
        self.workflow = workflow
        self.workflow_name = str(workflow.get("name") or "unnamed-workflow")
        self.workflow_id = f"n8n_workflow:{safe_id(rel_path, self.workflow_name)}"
        self.index_records: list[dict] = []
        self.detail_records: list[dict] = []
        self.connect_relations: list[dict] = []
        self.api_relations: list[dict] = []
        self.field_relations: list[dict] = []
        self.subworkflow_refs: list[tuple[str, str]] = []
        self.findings: dict[str, int] = {}
        self.totals = {
            "workflow_count": 1,
            "node_count": 0,
            "connection_count": 0,
            "trigger_count": 0,
            "credential_ref_count": 0,
            "redacted_value_count": 0,
        }
        self._node_id_by_name: dict[str, str] = {}
        self._node_type_by_name: dict[str, str] = {}
        self._credential_record_ids: dict[tuple[str, str], str] = {}
        self._endpoint_record_ids: dict[str, str] = {}
        self._has_error_handling = False

    def _finding(self, name: str) -> None:
        self.findings[name] = self.findings.get(name, 0) + 1

    def build(self) -> None:
        nodes = [node for node in self.workflow.get("nodes") or [] if isinstance(node, dict)]
        for node in nodes:
            self._build_node(node)
        self._build_connections()
        if not self._has_error_handling and not (self.workflow.get("settings") or {}).get("errorWorkflow"):
            self._finding("no_error_branch")
        pin_data = self.workflow.get("pinData")
        has_pin_data = bool(isinstance(pin_data, dict) and pin_data)
        if has_pin_data:
            self._finding("pin_data_present")
        self._build_workflow_record(nodes, has_pin_data)

    # ── nodes ────────────────────────────────────────────────────────────

    def _build_node(self, node: dict) -> None:
        rel_path = self.rel_path
        node_name = str(node.get("name") or f"node-{self.totals['node_count']}")
        node_type = str(node.get("type") or "unknown")
        node_record_id = f"n8n_node:{safe_id(rel_path, self.workflow_name, str(node.get('id') or node_name))}"
        self._node_id_by_name[node_name] = node_record_id
        self._node_type_by_name[node_name] = node_type
        self.totals["node_count"] += 1

        role_labels = _role_labels_for_type(node_type)
        if "trigger" in role_labels:
            self.totals["trigger_count"] += 1
        if "code" in role_labels:
            self._finding("code_node_present")
        if str(node.get("onError") or "").strip():
            self._has_error_handling = True

        parameters = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
        parameter_keys = sorted(str(key) for key in parameters.keys())[:_MAX_PARAMETER_SUMMARY_KEYS]
        params_summary, expression_refs = self._summarize_parameters(parameters)
        credential_refs = self._collect_credentials(node, node_record_id)

        self._build_role_relations(node_record_id, node_type, role_labels, parameters)
        self._build_field_relations(node_record_id, role_labels, expression_refs, parameters)

        http_target = self._describe_http_target(parameters) if "api_call" in role_labels else None
        llm_hint = node_type if "llm" in role_labels else None

        verbose = (
            f"n8n node {node_name} of type {node_type} in workflow {self.workflow_name} ({rel_path}). "
            f"Roles: {compact_list(role_labels) if role_labels else 'none'}. "
            f"Parameter keys: {compact_list(parameter_keys, 8)}. "
            + (f"Calls HTTP endpoint {http_target}. " if http_target else "")
            + (f"Uses LLM provider node {llm_hint}. " if llm_hint else "")
            + (f"Credentials: {compact_list([ref['type'] for ref in credential_refs])}. " if credential_refs else "")
            + (f"Data expressions: {compact_list(expression_refs, 6)}." if expression_refs else "")
        ).strip()
        compact = (
            f"n8n node {node_name} ({node_type}) roles {compact_list(role_labels, 3)}"
            + (f" http {compact_text(http_target, 60)}" if http_target else "")
        )

        self.index_records.append({
            "kind": "n8n_node",
            "file": rel_path,
            "id": node_record_id,
            "parent_id": self.workflow_id,
            "node_id": str(node.get("id") or node_name),
            "name": node_name,
            "workflow_name": self.workflow_name,
            "node_type": node_type,
            "type_version": node.get("typeVersion"),
            "parameter_keys": parameter_keys,
            "credential_refs": credential_refs,
            "expression_refs": expression_refs,
            "role_labels": role_labels,
            "summary": f"{node_type} node '{node_name}' in workflow '{self.workflow_name}'",
            "embedding_text": build_embedding_text(self.extractor.embedding_text_mode, verbose, compact),
        })
        self.detail_records.append({
            "kind": "n8n_node_detail",
            "file": rel_path,
            "id": f"n8n_node_detail:{safe_id(rel_path, self.workflow_name, str(node.get('id') or node_name))}",
            "parent_id": node_record_id,
            "node_id": str(node.get("id") or node_name),
            "name": node_name,
            "node_type": node_type,
            "parameters_summary": params_summary,
            "credential_refs": credential_refs,
            "expression_refs": expression_refs,
            "position": node.get("position"),
        })

    def _summarize_parameters(self, parameters: dict) -> tuple[dict, list[str]]:
        """Redacted, truncated per-key summary plus collected expression refs."""
        summary: dict[str, str] = {}
        expressions: list[str] = []

        def _scan_expressions(value: str) -> None:
            for match in _EXPRESSION_PATTERN.findall(value):
                if match not in expressions and len(expressions) < _MAX_EXPRESSION_REFS:
                    expressions.append(match)

        def _render(value: object, key_is_secret: bool) -> str:
            if isinstance(value, str):
                _scan_expressions(value)
                if key_is_secret or _looks_secret_value(value):
                    self.totals["redacted_value_count"] += 1
                    self._finding("hardcoded_secret_candidate")
                    return _REDACTED
                if value.startswith(("http://", "https://")):
                    value = _normalize_url(value)
                return value[:_MAX_PARAMETER_VALUE_CHARS]
            if isinstance(value, (int, float, bool)) or value is None:
                return str(value)
            if isinstance(value, dict):
                for nested_key, nested in value.items():
                    _render(nested, key_is_secret or bool(_SECRET_KEY_PATTERN.search(str(nested_key))))
                return f"<object:{len(value)} keys>"
            if isinstance(value, list):
                for item in value:
                    _render(item, key_is_secret)
                return f"<list:{len(value)} items>"
            return f"<{type(value).__name__}>"

        for key, value in list(parameters.items())[:_MAX_PARAMETER_SUMMARY_KEYS]:
            summary[str(key)] = _render(value, bool(_SECRET_KEY_PATTERN.search(str(key))))
        return summary, expressions

    def _collect_credentials(self, node: dict, node_record_id: str) -> list[dict]:
        credentials = node.get("credentials") if isinstance(node.get("credentials"), dict) else {}
        refs: list[dict] = []
        for cred_type, cred_value in credentials.items():
            cred_name = ""
            if isinstance(cred_value, dict):
                cred_name = str(cred_value.get("name") or "")
            elif isinstance(cred_value, str):
                cred_name = cred_value
            ref = {"name": cred_name or "unnamed", "type": str(cred_type)}
            refs.append(ref)
            self.totals["credential_ref_count"] += 1
            key = (ref["name"], ref["type"])
            record_id = self._credential_record_ids.get(key)
            if record_id is None:
                record_id = f"n8n_credential_ref:{safe_id(self.rel_path, self.workflow_name, ref['name'], ref['type'])}"
                self._credential_record_ids[key] = record_id
                self.index_records.append({
                    "kind": "n8n_credential_ref",
                    "file": self.rel_path,
                    "id": record_id,
                    "parent_id": self.workflow_id,
                    "name": ref["name"],
                    "credential_type": ref["type"],
                    "summary": f"Credential reference '{ref['name']}' of type {ref['type']}",
                    "embedding_text": build_embedding_text(
                        self.extractor.embedding_text_mode,
                        f"n8n credential reference {ref['name']} of type {ref['type']} in workflow {self.workflow_name}. Values are never exported.",
                        f"n8n credential {ref['name']} ({ref['type']})",
                    ),
                })
            self.api_relations.append({"from": node_record_id, "to": record_id, "type": "n8n_uses_credential_ref"})
        return refs

    # ── role-driven relations ────────────────────────────────────────────

    def _build_role_relations(self, node_record_id: str, node_type: str, role_labels: list[str], parameters: dict) -> None:
        if "webhook" in role_labels:
            webhook_path = str(parameters.get("path") or "")
            relation = {"from": self.workflow_id, "to": node_record_id, "type": "n8n_receives_webhook"}
            if webhook_path:
                relation["endpoint_path"] = webhook_path
            self.api_relations.append(relation)
            auth = str(parameters.get("authentication") or "").strip().lower()
            if auth in ("", "none"):
                self._finding("public_webhook_without_auth_hint")
        if "api_call" in role_labels:
            endpoint_record_id = self._ensure_endpoint_record(parameters)
            if endpoint_record_id:
                relation = {"from": node_record_id, "to": endpoint_record_id, "type": "n8n_calls_http_endpoint"}
                method = str(parameters.get("method") or parameters.get("requestMethod") or "GET").upper()
                relation["http_method"] = method
                url = self._describe_http_target(parameters)
                if url:
                    relation["endpoint_path"] = url
                self.api_relations.append(relation)
        if "llm" in role_labels:
            self.api_relations.append({"from": node_record_id, "to": self.workflow_id, "type": "n8n_uses_llm_provider"})
        if "subworkflow" in role_labels:
            target = parameters.get("workflowId")
            if isinstance(target, dict):
                target = target.get("cachedResultName") or target.get("value")
            target_name = str(target or parameters.get("workflow") or "").strip()
            if target_name:
                self.subworkflow_refs.append((node_record_id, target_name))

    def _describe_http_target(self, parameters: dict) -> str | None:
        url = parameters.get("url") or parameters.get("uri")
        if not isinstance(url, str) or not url.strip():
            return None
        return _normalize_url(url.strip())[:_MAX_PARAMETER_VALUE_CHARS]

    def _ensure_endpoint_record(self, parameters: dict) -> str | None:
        url = self._describe_http_target(parameters)
        if not url:
            return None
        record_id = self._endpoint_record_ids.get(url)
        if record_id is None:
            record_id = f"n8n_http_endpoint:{safe_id(self.rel_path, self.workflow_name, url)}"
            self._endpoint_record_ids[url] = record_id
            self.index_records.append({
                "kind": "n8n_http_endpoint",
                "file": self.rel_path,
                "id": record_id,
                "parent_id": self.workflow_id,
                "name": url,
                "summary": f"External HTTP endpoint {url} used by workflow '{self.workflow_name}'",
                "embedding_text": build_embedding_text(
                    self.extractor.embedding_text_mode,
                    f"External HTTP endpoint {url} called from n8n workflow {self.workflow_name} in {self.rel_path}.",
                    f"HTTP endpoint {compact_text(url, 80)}",
                ),
            })
        return record_id

    def _build_field_relations(self, node_record_id: str, role_labels: list[str], expression_refs: list[str], parameters: dict) -> None:
        for ref in expression_refs:
            if ref.startswith("$json"):
                self.field_relations.append({
                    "from": node_record_id,
                    "to": self.workflow_id,
                    "type": "n8n_reads_data_field",
                    "field": ref,
                })
        assignments = parameters.get("assignments")
        if isinstance(assignments, dict):
            entries = assignments.get("assignments")
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("name"):
                        self.field_relations.append({
                            "from": node_record_id,
                            "to": self.workflow_id,
                            "type": "n8n_writes_data_field",
                            "field": str(entry["name"]),
                        })

    # ── connections ──────────────────────────────────────────────────────

    def _build_connections(self) -> None:
        connections = self.workflow.get("connections") if isinstance(self.workflow.get("connections"), dict) else {}
        for source_name, groups in connections.items():
            source_id = self._node_id_by_name.get(str(source_name))
            if not source_id or not isinstance(groups, dict):
                continue
            source_type = self._node_type_by_name.get(str(source_name), "").lower()
            for group_key, outputs in groups.items():
                if not isinstance(outputs, list):
                    continue
                for output_index, targets in enumerate(outputs):
                    if not isinstance(targets, list):
                        continue
                    for target in targets:
                        if not isinstance(target, dict):
                            continue
                        target_id = self._node_id_by_name.get(str(target.get("node")))
                        if not target_id:
                            continue
                        edge_type = self._classify_edge(str(group_key), output_index, source_type)
                        self.connect_relations.append({"from": source_id, "to": target_id, "type": edge_type})
                        self.totals["connection_count"] += 1
                        if edge_type == "n8n_error_flow":
                            self._has_error_handling = True

    def _classify_edge(self, group_key: str, output_index: int, source_type: str) -> str:
        lowered_key = group_key.lower()
        if lowered_key == "error":
            return "n8n_error_flow"
        if lowered_key.startswith("ai_"):
            return "n8n_ai_tool_flow"
        if source_type.endswith(".if"):
            return "n8n_branch_true" if output_index == 0 else "n8n_branch_false"
        if source_type.endswith(".wait"):
            return "n8n_resume_flow"
        return "n8n_connects"

    # ── workflow record ──────────────────────────────────────────────────

    def _build_workflow_record(self, nodes: list[dict], has_pin_data: bool) -> None:
        findings = sorted(self.findings.keys())
        node_names = [str(node.get("name") or "") for node in nodes if node.get("name")]
        verbose = (
            f"n8n workflow {self.workflow_name} in file {self.rel_path}. "
            f"Active: {bool(self.workflow.get('active'))}. "
            f"Nodes: {self.totals['node_count']} ({compact_list(node_names, 10)}). "
            f"Connections: {self.totals['connection_count']}. Triggers: {self.totals['trigger_count']}. "
            f"Credential references: {self.totals['credential_ref_count']}. "
            f"Security findings: {compact_list(findings) if findings else 'none'}."
        )
        compact = (
            f"n8n workflow {self.workflow_name}: {self.totals['node_count']} nodes, "
            f"{self.totals['trigger_count']} triggers, findings {compact_list(findings, 3)}"
        )
        self.index_records.insert(0, {
            "kind": "n8n_workflow",
            "file": self.rel_path,
            "id": self.workflow_id,
            "name": self.workflow_name,
            "workflow_name": self.workflow_name,
            "active": bool(self.workflow.get("active")),
            "node_count": self.totals["node_count"],
            "connection_count": self.totals["connection_count"],
            "trigger_count": self.totals["trigger_count"],
            "credential_ref_count": self.totals["credential_ref_count"],
            "tags": [str(tag.get("name") if isinstance(tag, dict) else tag) for tag in (self.workflow.get("tags") or [])],
            "has_pin_data": has_pin_data,
            "security_findings": findings,
            "summary": f"n8n workflow '{self.workflow_name}' with {self.totals['node_count']} nodes",
            "embedding_text": build_embedding_text(self.extractor.embedding_text_mode, verbose, compact),
        })

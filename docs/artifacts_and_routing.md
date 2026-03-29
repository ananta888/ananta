# Artifact ingestion and routing decision traceability

Artifact ingestion API (additive)

POST /api/v1/artifacts
{
  "goal_id": "G-...",
  "plan_node_id": "PN-...",
  "task_id": "T-...",
  "artifact_type": "report|code|coverage|verification",
  "content": { /* base64 or structured payload */ },
  "metadata": { /* optional key-values */ }
}

Response: 201 Created with artifact id and minimal lineage information.

Routing decision persistence

Store model and routing metadata for each planning or execution decision so it is inspectable:

- model_name
- model_version
- selection_reason (e.g., "capability_match", "policy_preference")
- policy_version
- timestamp

This information should be attached to plan nodes, execution traces and artifact records so consumers can explain why a particular model or worker was chosen.

Least-privilege result views

- Default goal detail views expose artifact/result summaries, trace references and aggregate governance counts.
- Detailed policy decision payloads and verification records are restricted to authorized operators.
- Team-scoped goal reads apply to goal detail, plan inspection and derived artifact/trace summaries.

Tamper-evident governance trail

- Critical goal, plan, policy and verification events are written to the audit log with `prev_hash` and `record_hash`.
- The resulting chain allows later integrity checks without changing the hub-owned orchestration model.

Knowledge indexing and routing traceability

- Artefakte koennen ueber kontrollierte Hub-Profile indexiert werden: `default`, `fast_docs`, `deep_code`.
- Index-Laeufe speichern:
  - `profile_name`
  - `manifest_summary`
  - `artifact_version_id`
  - `knowledge_index_id`
  - `run_id`
- SGPT-/RAG-Kontext liefert Explainability fuer:
  - `collection_names`
  - `artifact_ids`
  - `knowledge_index_ids`
  - `chunk_types`
- Fuer groessere Laeufe stehen asynchrone Statuspfade bereit:
  - `POST /artifacts/<id>/rag-index` mit `{ "async": true }`
  - `POST /knowledge/collections/<id>/index` mit `{ "async": true }`
  - `GET /artifacts/<id>/rag-jobs/<job_id>`
  - `GET /knowledge/index-jobs/<job_id>`

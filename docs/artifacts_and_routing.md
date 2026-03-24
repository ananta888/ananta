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

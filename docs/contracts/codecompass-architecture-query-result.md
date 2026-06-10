# Contract: codecompass_architecture_query_result.v1

Antwortvertrag der Architecture Query Engine
(`GET /api/codecompass/query`, `worker/retrieval/codecompass_architecture_query.py`).

## Top-Level-Felder

| Feld | Typ | Bedeutung |
|---|---|---|
| `schema` | string | immer `codecompass_architecture_query_result.v1` |
| `query_type` | string | einer der whitelisted Query-Typen |
| `seed` | object | `input`, optional `field`, `resolved_node_ids`, `candidates` |
| `results` | array | Treffer mit Evidence-Pfaden (siehe unten) |
| `diagnostics` | object | `bounded`, `applied_limits`, `depth_used`, `query_direction`, Traversal-Zähler |
| `warnings` | array | z. B. `seed_not_resolved`, `ambiguous_seed`, `depth_clamped_to_max`, `calls_probable_target edges are heuristic` |
| `error` | string | nur bei `invalid_query_type` (plus `valid_query_types`) |

Wichtige Unterscheidung (CCAQE-001):

- `seed.resolved_node_ids` — worauf die Eingabe aufgelöst wurde (Startpunkte),
- `results[].result_node_id` — gefundene Knoten,
- `results[].evidence_paths` — der Beweis, warum ein Knoten als relevant gilt.

## Result-Einträge

Jeder Eintrag enthält: `result_node_id`, `result_kind`, `result_role`, `score`,
`depth`, `evidence_paths` (je `path_score` + `edges`), `warnings`. Jede Edge:
`source_id`, `target_id`, `edge_type`, `direction_used`, `confidence`
(+ optional `field`, `operation`, `heuristic`).

Query-spezifische Zusatzfelder: `coverage_kind` (controller-test-coverage),
`enforcement` + `operations` (field-policy-impact), `dependency_kind` +
`transactional_boundary` (service-dependency-chain).

Heuristische Kanten (`calls_probable_target`, `test_calls_endpoint`) erzeugen
Warnings — Ergebnisse, die nur darauf beruhen, tragen `heuristic_evidence_only`.

## Beispiel: dto-impact

```json
{
  "schema": "codecompass_architecture_query_result.v1",
  "query_type": "dto-impact",
  "seed": {
    "input": "UserDto",
    "field": null,
    "resolved_node_ids": ["java_type:src/main/java/example/UserDto.java:UserDto"],
    "candidates": [{"node_id": "java_type:src/main/java/example/UserDto.java:UserDto", "score": 0.95, "reason": "name_exact"}]
  },
  "results": [
    {
      "result_node_id": "java_type:src/main/java/example/UserService.java:UserService",
      "result_kind": "java_type",
      "result_role": "service",
      "score": 0.95,
      "depth": 1,
      "evidence_paths": [
        {
          "path_score": 0.95,
          "edges": [
            {
              "source_id": "java_type:src/main/java/example/UserService.java:UserService",
              "target_id": "java_type:src/main/java/example/UserDto.java:UserDto",
              "edge_type": "field_type_uses",
              "direction_used": "incoming",
              "confidence": 0.95
            }
          ]
        }
      ],
      "warnings": []
    }
  ],
  "diagnostics": {
    "bounded": true,
    "applied_limits": {"max_depth": 4, "max_nodes": 200, "max_results": 25, "max_paths_per_result": 3},
    "query_direction": "incoming",
    "depth_used": 3,
    "traversed_nodes": 6,
    "expansions": 9,
    "truncated": false,
    "cycle_count": 0
  },
  "warnings": []
}
```

## Beispiel: controller-test-coverage

```json
{
  "schema": "codecompass_architecture_query_result.v1",
  "query_type": "controller-test-coverage",
  "seed": {"input": "UserController", "field": null, "resolved_node_ids": ["java_type:src/main/java/example/UserController.java:UserController"], "candidates": [{"node_id": "java_type:src/main/java/example/UserController.java:UserController", "score": 0.95, "reason": "name_exact"}]},
  "results": [
    {
      "result_node_id": "java_type:src/test/java/example/UserControllerTest.java:UserControllerTest",
      "result_kind": "java_type",
      "result_role": "test",
      "coverage_kind": "direct_controller_test",
      "score": 1.0,
      "depth": 1,
      "evidence_paths": [
        {"path_score": 1.0, "edges": [{"source_id": "java_type:src/test/java/example/UserControllerTest.java:UserControllerTest", "target_id": "java_type:src/main/java/example/UserController.java:UserController", "edge_type": "test_targets_type", "direction_used": "incoming", "confidence": 1.0}]}
      ],
      "warnings": []
    },
    {
      "result_node_id": "java_type:src/test/java/example/UserApiIT.java:UserApiIT",
      "result_kind": "java_type",
      "result_role": "test",
      "coverage_kind": "endpoint_test",
      "score": 0.5168,
      "depth": 2,
      "evidence_paths": [
        {"path_score": 0.5168, "edges": [
          {"source_id": "java_type:src/main/java/example/UserController.java:UserController", "target_id": "java_method:src/main/java/example/UserController.java:getUsers", "edge_type": "controller_endpoint_declares", "direction_used": "outgoing", "confidence": 0.95},
          {"source_id": "java_type:src/test/java/example/UserApiIT.java:UserApiIT", "target_id": "java_method:src/main/java/example/UserController.java:getUsers", "edge_type": "test_calls_endpoint", "direction_used": "incoming", "confidence": 0.8}
        ]}
      ],
      "warnings": []
    }
  ],
  "diagnostics": {"bounded": true, "applied_limits": {"max_depth": 4, "max_nodes": 200, "max_results": 25, "max_paths_per_result": 3}, "query_direction": "both", "depth_used": 3},
  "warnings": []
}
```

## Beispiel: field-policy-impact

```json
{
  "schema": "codecompass_architecture_query_result.v1",
  "query_type": "field-policy-impact",
  "seed": {"input": "UserDto", "field": "price", "resolved_node_ids": ["java_type:src/main/java/example/UserDto.java:UserDto"], "candidates": [{"node_id": "java_type:src/main/java/example/UserDto.java:UserDto", "score": 0.95, "reason": "name_exact"}]},
  "results": [
    {
      "result_node_id": "java_type:src/main/java/example/security/PriceFieldPolicy.java:PriceFieldPolicy",
      "result_kind": "java_type",
      "result_role": "policy",
      "enforcement": "enforced_backend_guard",
      "operations": ["update"],
      "score": 0.95,
      "depth": 1,
      "evidence_paths": [
        {"path_score": 0.95, "edges": [{"source_id": "java_type:src/main/java/example/security/PriceFieldPolicy.java:PriceFieldPolicy", "target_id": "java_type:src/main/java/example/UserDto.java:UserDto", "edge_type": "policy_applies_to_field", "direction_used": "incoming", "confidence": 0.95, "field": "price", "operation": "update"}]}
      ],
      "warnings": []
    },
    {
      "result_node_id": "ts_file:frontend/src/app/user-form.guard.ts:UserFormGuard",
      "result_kind": "ts_file",
      "result_role": "config",
      "enforcement": "frontend_reference",
      "score": 0.765,
      "depth": 1,
      "evidence_paths": [
        {"path_score": 0.765, "edges": [{"source_id": "ts_file:frontend/src/app/user-form.guard.ts:UserFormGuard", "target_id": "java_type:src/main/java/example/UserDto.java:UserDto", "edge_type": "frontend_guard_refs_field", "direction_used": "incoming", "confidence": 0.85, "field": "price"}]}
      ],
      "warnings": []
    }
  ],
  "diagnostics": {"bounded": true, "applied_limits": {"max_depth": 4, "max_nodes": 200, "max_results": 25, "max_paths_per_result": 3}, "query_direction": "incoming", "depth_used": 3},
  "warnings": []
}
```

## Beispiel: service-dependency-chain

```json
{
  "schema": "codecompass_architecture_query_result.v1",
  "query_type": "service-dependency-chain",
  "seed": {"input": "UserService", "field": null, "resolved_node_ids": ["java_type:src/main/java/example/UserService.java:UserService"], "candidates": [{"node_id": "java_type:src/main/java/example/UserService.java:UserService", "score": 0.95, "reason": "name_exact"}]},
  "results": [
    {
      "result_node_id": "java_type:src/main/java/example/UserRepository.java:UserRepository",
      "result_kind": "java_type",
      "result_role": "repository",
      "dependency_kind": "direct_dependency",
      "transactional_boundary": true,
      "score": 0.9025,
      "depth": 1,
      "evidence_paths": [
        {"path_score": 0.9025, "edges": [{"source_id": "java_type:src/main/java/example/UserService.java:UserService", "target_id": "java_type:src/main/java/example/UserRepository.java:UserRepository", "edge_type": "service_uses_repository", "direction_used": "outgoing", "confidence": 0.95}]},
        {"path_score": 0.81, "edges": [{"source_id": "java_type:src/main/java/example/UserService.java:UserService", "target_id": "java_type:src/main/java/example/UserRepository.java:UserRepository", "edge_type": "transactional_boundary", "direction_used": "outgoing", "confidence": 0.9}]}
      ],
      "warnings": []
    }
  ],
  "diagnostics": {
    "bounded": true,
    "applied_limits": {"max_depth": 4, "max_nodes": 200, "max_results": 25, "max_paths_per_result": 3},
    "query_direction": "outgoing",
    "depth_used": 3,
    "service_dependency_cycles_detected": 1
  },
  "warnings": ["calls_probable_target edges are heuristic"]
}
```

## Leeres Ergebnis (valide)

```json
{
  "schema": "codecompass_architecture_query_result.v1",
  "query_type": "dto-impact",
  "seed": {"input": "DoesNotExist", "field": null, "resolved_node_ids": [], "candidates": []},
  "results": [],
  "diagnostics": {"bounded": true, "applied_limits": {"max_depth": 4, "max_nodes": 200, "max_results": 25, "max_paths_per_result": 3}},
  "warnings": ["seed_not_resolved"]
}
```

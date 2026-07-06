# CodeCompass: n8n-Workflow-Verständnis

Stand: 2026-07-06 · Track: `codecompass-n8n-workflow-understanding`

Der rag-helper indexiert exportierte n8n-Workflow-JSONs als **Prozessgraph**
statt als Rohtext: Nodes, Datenfluss, Branches, API-Kanten, Credentials-Referenzen
und Security-Findings werden als CodeCompass-Records exportiert. Der n8n-Export
ist dabei eine Momentaufnahme — der Original-Workflow in n8n bleibt die
autoritative Quelle. Es wird nichts ausgeführt, nichts nach n8n deployt und
kein n8n-Server benötigt.

## Aktivierung (opt-in)

`json` steht bewusst **nicht** in den DEFAULT_EXTENSIONS — bestehende Läufe
ändern sich nicht. Aktivierung per Flag oder Profil:

```bash
# per Flag
python rag-helper/codecompass_rag.py /pfad/zum/workflow-repo --extensions json md --dry-run

# per Profil (empfohlen, filtert package.json/tsconfig/lockfiles)
python rag-helper/codecompass_rag.py /pfad/zum/workflow-repo \
  --config rag-helper/profiles/n8n-workflows.json -o rag_out
```

Nicht-n8n-JSON (package.json, tsconfig, …) wird automatisch erkannt und mit
`skip_reason=not_n8n_workflow` übersprungen; kaputtes JSON mit
`skip_reason=invalid_json`. Beides bricht den Lauf nie ab.

**Hinweis pinData/Dateigröße:** `max_file_size_kb` greift vor dem Parsen.
Workflow-Exporte mit großem `pinData` (gepinnte Beispieldaten) können das
Limit reißen und werden dann komplett geskippt — pinData vor dem Indexieren
entfernen oder das Limit anheben.

## Was extrahiert wird

| Record-Kind | Inhalt |
|---|---|
| `n8n_workflow` | name, active, node_count, connection_count, trigger_count, credential_ref_count, tags, has_pin_data, security_findings |
| `n8n_node` | node_id, name, node_type, type_version, parameter_keys (nur Schlüssel), credential_refs (name/type), expression_refs, role_labels |
| `n8n_node_detail` | redactete, gekürzte Parameter-Zusammenfassung, Position |
| `n8n_credential_ref` | Credential-Referenz als eigener Graph-Knoten (nur name/type) |
| `n8n_http_endpoint` | externes HTTP-Ziel (normalisierte URL) als eigener Graph-Knoten |

Rollen-Heuristik (`role_labels`): trigger, webhook, api_call, llm, branch,
merge, wait, code, subworkflow, email.

## Relations (Kanten)

Direktes `from`/`to`/`type`-Format; `field`, `http_method` und `endpoint_path`
erscheinen als Kantenattribute:

| Relation-Type | Bedeutung |
|---|---|
| `n8n_connects` | main-Datenfluss zwischen Nodes |
| `n8n_branch_true` / `n8n_branch_false` | IF-Node-Ausgänge |
| `n8n_error_flow` | Error-Ausgang |
| `n8n_ai_tool_flow` | ai_*-Verbindungen (z. B. Language Model → Agent) |
| `n8n_resume_flow` | Fortsetzung nach Wait-Node |
| `n8n_receives_webhook` | Workflow → Webhook-Node (mit endpoint_path) |
| `n8n_calls_http_endpoint` | Node → `n8n_http_endpoint` (mit http_method/endpoint_path) |
| `n8n_invokes_subworkflow` | Node → lokal aufgelöster Ziel-Workflow; nicht-lokale Ziele erzeugen keine hängenden Kanten |
| `n8n_uses_credential_ref` | Node → `n8n_credential_ref` |
| `n8n_uses_llm_provider` | LLM-Node → Workflow |
| `n8n_reads_data_field` / `n8n_writes_data_field` | $json-Lesezugriffe bzw. Set-Zuweisungen (field-Attribut) |

Workflow→Node-Kanten (`parent_child`) entstehen automatisch aus `parent_id`.
Bei `max_relation_records_per_file` überleben Datenfluss-Kanten zuerst
(Prioritäten in `relation_compaction.py`), Datenfeld-Kanten fallen zuerst weg.

## Redaction und Credentials (was NICHT exportiert wird)

- Parameterwerte, deren Schlüssel nach Secret aussieht (token, apiKey,
  authorization, password, secret, bearer, credential, privateKey, accessKey),
  werden als `<redacted>` gespeichert; ebenso Werte, die nach Token aussehen
  (Bearer …, sk-/ghp_-Präfixe, lange Base64/Hex-Strings).
- Credentials erscheinen nur als Referenz (name/type) — nie Werte, nie IDs.
- Query-Parameter mit Secret-Verdacht werden aus URLs entfernt
  (`?token=…` verschwindet, `?limit=10` bleibt).
- `pinData`-Inhalte werden nie gelesen/exportiert (nur `has_pin_data`-Flag),
  da sie echte Payload-Daten enthalten können.
- Jeder Fund zählt als `hardcoded_secret_candidate` in den security_findings.

Security-Findings auf dem Workflow-Record: `hardcoded_secret_candidate`,
`public_webhook_without_auth_hint`, `code_node_present`, `no_error_branch`,
`pin_data_present`. Sie stehen auch im embedding_text und sind damit
retrieval-auffindbar.

## Beispielfragen für CodeCompass-Retrieval

- „Welcher Webhook startet den Prozess?" → `n8n_receives_webhook` + webhook-Rolle
- „Welche Nodes nutzen Credentials?" → `n8n_uses_credential_ref`-Kanten
- „Wo geht der False-Branch hin?" → `n8n_branch_false`-Kante des IF-Nodes
- „Welche APIs werden aufgerufen?" → `n8n_http_endpoint`-Knoten
- „Welche Workflows haben keinen Error-Branch?" → Finding `no_error_branch`
- „Welche Workflows enthalten LLM-Nodes?" → Rolle `llm` / `n8n_uses_llm_provider`

## Tests

```bash
docker compose -p compose-next -f docker/compose-next/compose.tests.lmstudio.yml \
  run --rm t-infra sh -c "cd rag-helper && python -m pytest -q tests/test_n8n_workflow_extractor.py"
```

Fixtures unter `rag-helper/tests/fixtures/n8n/` decken Manual→HTTP,
Webhook→IF→Merge→Wait, LLM-Triage, Secrets+pinData und ein
package.json-Negativbeispiel ab.

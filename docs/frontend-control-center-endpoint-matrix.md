# Frontend Control Center Endpoint-Matrix (Ist/Soll)

Stand: 2026-06-01

## Legende
- `exists`: Endpoint existiert und ist direkt nutzbar.
- `adapter_needed`: Endpoint existiert teilweise; UI braucht Mapping/Adapter.
- `missing`: Endpoint fehlt und muss ergänzt werden.

## Matrix
| Soll-Endpoint | Status | Ist-Hinweis | Folgeaktion |
|---|---|---|---|
| `GET /api/projects` | missing | Kein dedizierter Projekt-Index im Hub-API-Client | `api/projects` Read-Endpoint ergänzen |
| `GET /api/projects/{projectId}/tasks` | missing | Tasks vorhanden, aber nicht projektgebunden in UI-Client | Projektfilter im Backend + Client ergänzen |
| `GET /api/tasks/{taskId}` | adapter_needed | Task-Detail-Endpunkte vorhanden, aber verteilt | einheitliches Task-Detail-DTO einführen |
| `POST /api/tasks` | adapter_needed | Task-Erzeugung vorhanden, teilweise anderer Payload | Control-Center-Create-Payload adapter |
| `PATCH /api/tasks/{taskId}` | adapter_needed | Teilweise Updates vorhanden | Status-/Metadaten-Update vereinheitlichen |
| `GET /api/sessions` | missing | Session-ähnliche Daten über Share/WebRTC/Terminal verteilt | Session-Read-Model-Endpoint ergänzen |
| `GET /api/sessions/{sessionId}` | missing | kein einheitlicher Session-Detail-Endpunkt | Session-Detail-DTO ergänzen |
| `POST /api/tasks/{taskId}/sessions` | missing | Delegation/Orchestration vorhanden, aber kein UI-startbarer Session-Endpoint | kontrollierten Start-Endpoint ergänzen |
| `POST /api/sessions/{sessionId}/cancel` | missing | Teilweise über Task-/Terminal-APIs abbildbar | dedizierte Cancel-Operation ergänzen |
| `GET /api/workers` | adapter_needed | Agent/Worker-Infos über Dashboard/System verfügbar | WorkerRegistry-DTO ableiten |
| `GET /api/policies` | adapter_needed | Policy-Daten in Context-Policy vorhanden | vereinheitlichte Policy-Liste + Versionen |
| `GET /api/sessions/{sessionId}/policy-decisions` | missing | Decision-Informationen nur indirekt | Decision-Log-Endpoint ergänzen |
| `POST /api/policy/approve` | adapter_needed | Freigaben vorhanden, aber nicht als allgemeiner Approval-Endpunkt | enger Approval-Contract |
| `GET /api/artifacts` | exists | `hub-artifacts-api.client.ts` vorhanden | direkt nutzen |
| `GET /api/artifacts/{artifactId}` | exists | vorhanden | direkt nutzen |
| `GET /api/artifacts/{artifactId}/content` | adapter_needed | Metadaten vorhanden, Content-Varianten teils unterschiedlich | Viewer-Content-Adapter |
| `GET /api/codecompass/context-scopes` | adapter_needed | Context-Policy Endpunkte vorhanden | Scope-Liste normalisieren |
| `POST /api/codecompass/context-scopes/preview` | missing | Preview-Endpunkt fehlt als einheitlicher Contract | Preview-Endpoint ergänzen |
| `GET /api/events/stream` | missing | Teilweise Polling/SSE in Teilbereichen | zentralen Event-Stream-Endpoint ergänzen |

## Fazit
Das MVP kann mit vorhandenen Endpunkten starten, wenn UI-seitig Adapter genutzt werden. Für den vollen Control-Center-Fluss sind insbesondere `sessions`, `policy-decisions` und `events/stream` als konsistente Read-Model-Schnittstellen nachzuziehen.

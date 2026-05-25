# Course Source Mapping

## Ziel

Bestehende Quellen auf Kursmodule mappen, damit Inhalte wiederverwendet und nicht doppelt dokumentiert werden.

## Mapping

| Kursmodul | Primärquellen | Bestehender Stand | Noch zu erstellen |
|---|---|---|---|
| Hub-Worker Grundlagen | `AGENTS.md`, `docs/security/encrypted-artifact-exchange.md` | Architektur- und Governance-Regeln vorhanden | didaktische Kursstruktur (`course.json`, Lessons, Exercises) |
| Deterministische Policies | `worker/core/context_access_policy.py`, `docs/security/context-release-gates.md`, `docs/security/artifact-object-policy.md` | Policy-Prinzipien und Gates vorhanden | Lernmodule mit Fallübungen |
| Artefaktfreigaben & Audit | `docs/security/artifact-access-grants.md`, `docs/security/artifact-audit-events.md`, `docs/security/artifact-grant-revocation.md` | Grant-/Audit-Design vorhanden | kursbezogene Übungen und Assessments |
| RAG & sichere Kontextfreigaben | `agent/services/context_bundle_service.py`, `docs/security/rag-artifact-release-policy.md` | Filter- und Freigabekonzept vorhanden | didaktische Sequenz inkl. Praxischecks |
| OIDC & Identitaet | `agent/routes/auth_oidc.py`, `docs/security/oidc.md` | Identity-Basis vorhanden | Kursmaterial mit Claim-/Grant-Uebungen |
| Strategie-Game Grundlagen | `docs/examples/ananta-game/README.md`, `README.figure-agents.md` | Spielkonzepte und To-dos vorhanden | strukturierte Lernmodule |
| WebRTC/P2P Grundlagen | `docs/examples/ananta-game/todo.webrtc-p2p-match-network.json`, `docs/security/webrtc-artifact-transfer.md` | Transport- und P2P-Konzepte vorhanden | Kurspfad mit sicheren Defaults |

## Wiederverwendbare Uebungs-Szenarien

1. allow/deny-Entscheidungen zu `provide_to_worker` vs `provide_to_remote_llm`
2. Revocation- und Expiry-Fälle aus Artefakt-Grant-Dokumentation
3. Nagabanda-/Regelvalidierung als Beispiel fuer deterministische statt probabilistische Entscheidungen

## Duplikationsregel

Kurse referenzieren bestehende Security-Dokumente als fachliche Quelle; sie kopieren diese Inhalte nicht 1:1, sondern ergaenzen nur didaktische Struktur, Uebungen und Checks.

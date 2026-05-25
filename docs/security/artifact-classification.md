# Artifact Classification

## Klassen

| Klasse | Zweck | Erlaubte Speicherorte | Erlaubte Transporte | Worker-Zugriff | Remote-LLM |
|---|---|---|---|---|---|
| `public` | Oeffentliche Inhalte | Hub/Object-Storage, lokal | Hub, P2P | erlaubt nach Policy | erlaubt nach Policy |
| `internal` | Team-interne Infos | Hub/Object-Storage, lokal | Hub, optional P2P | erlaubt nach Grant | nur mit explizitem Recht |
| `restricted` | Erhoehte Vertraulichkeit | verschluesselt, lokal kontrolliert | Hub, optional P2P (verschluesselt) | strikt grant-gebunden | default deny |
| `secret` | Hochsensitive Daten | verschluesselt, minimierte Replikation | nur verschluesselt | nur taskgebunden + zeitlich eng | deny (ausser expliziter Sonderfreigabe) |
| `local_only` | darf Standort nicht verlassen | lokal / kontrolliert auf Zielhost | kein externer Transfer | nur lokale Runtimes | deny |

## Mapping in `artifact_metadata`

Empfohlene Felder:

- `classification`: `public|internal|restricted|secret|local_only`
- `classification_reason`
- `classification_source` (user, policy, inherited)
- `remote_llm_allowed` (bool)
- `allowed_transfer_modes` (Liste)
- `max_release_scope` (z. B. `task`)

## Vererbungsregeln

- Worker-Resultate erben mindestens die staerkste Eingabeklasse.
- `local_only` bleibt `local_only`, ausser explizite Re-Klassifikation mit Audit.
- Klassifikation wird durch Worker nicht allein "heruntergestuft".


# Ananta Strategy Game Domain-Modell

## Zweck

Das Domain-Modell beschreibt eine serialisierbare, UI-unabhaengige Spielsicht auf Architektur, Rollen, Policies und Evidence.

## Kernobjekte

| Objekt | Zweck |
| --- | --- |
| `GameMap` | Container fuer Territorien, Agenten, Policy-Gates, Artifact-Ziele und Trust-Kanten |
| `CodeTerritory` | Abbildung eines Repository-Pfads/Moduls inkl. Risiko-Metadaten |
| `AgentUnit` | Rolle, Capabilities, erlaubter Kontext und Risikostufe |
| `PolicyNode` | Policy-Regel mit Effekt (`allow`, `deny`, `review_required`) |
| `ContextGate` | Sichtbarkeit/Freigabe fuer Territorien pro Rollenmenge |
| `ArtifactObjective` | Task-Abschlussziel inkl. Verification-/Evidence-Anforderung |
| `TrustEdge` | Vertrauens- oder Abhaengigkeitsbeziehung zwischen Knoten |

## Modellprinzipien

1. Keine Modellklasse benoetigt externe Services im Konstruktor.
2. IDs sind stabil und explizit.
3. Serialisierung erfolgt ueber `to_dict`/`from_dict` bzw. JSON.
4. Das Modell ist neutral gegenueber TUI/Web/IDE-Darstellung.

## Beispiel

```json
{
  "id": "map:demo",
  "title": "Ananta Strategy Map",
  "territories": [
    {
      "id": "territory:agent-services",
      "name": "agent/services",
      "path": "agent/services",
      "risk_level": "high"
    }
  ],
  "agents": [
    {
      "id": "agent:planner",
      "role": "planner",
      "capabilities": ["plan", "analyze"],
      "allowed_context": ["repo:index"]
    }
  ],
  "degraded": false
}
```

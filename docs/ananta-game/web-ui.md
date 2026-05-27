# Strategy Game Web-UI Vertrag

## Ziel

Die Web-UI rendert eine GameMap als Demo ohne echte Agenten-Ausfuehrung. Entscheidungen bleiben im Backend-/Modell-Layer.

## Minimaler JSON-Vertrag

```json
{
  "id": "map:demo-ui",
  "title": "Ananta Strategy Demo Map",
  "territories": [
    {
      "id": "t1",
      "name": "agent/services",
      "path": "agent/services",
      "visibility": "visible",
      "riskLevel": "high"
    }
  ],
  "agents": [
    { "id": "a1", "role": "hub", "capabilities": ["plan", "delegate", "approve"] }
  ],
  "policies": [
    { "id": "p1", "effect": "deny", "scope": ["secret_paths", "cloud_worker"] }
  ],
  "contextGates": [
    { "territoryId": "t1", "visibility": "visible", "localOnly": true, "secret": false }
  ],
  "artifacts": [
    { "id": "artifact:1", "taskId": "ASG-011", "status": "verified" }
  ],
  "degraded": false
}
```

## Darstellungsregeln (2D, barrierearm)

1. Territorien sind als Karten/Listen lesbar, nicht nur als Canvas.
2. Sichtbarkeit wird klar markiert:
   - `visible`
   - `blocked` / `hidden` / `redacted`
3. Gefaehrdete Territorien (`high`/`critical`) werden visuell unterscheidbar markiert.
4. UI erzeugt keine eigenen Policy-Entscheidungen.

## Implementierungsstatus

- Demo-Route: `strategy-game-demo`
- Demo-Komponente rendert statische Vertragsdaten aus `GameMapUiContract`.
- Kein Live-Backend zwingend erforderlich.

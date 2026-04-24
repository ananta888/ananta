# ml-intern Capability Profile

`ml_intern` ist ein optionales spezialisiertes Worker-Profil fuer ML-nahe Recherche-/Analyseaufgaben.

## Konfiguration

Pfad in `AGENT_CONFIG`:

```json
{
  "specialized_worker_profiles": {
    "enabled": true,
    "profiles": {
      "ml_intern": {
        "enabled": true,
        "backend_type": "external_worker",
        "capability_classes": ["ml_research", "research"],
        "risk_class": "medium",
        "requires_approval": true,
        "available": false,
        "routing_aliases": ["ml-intern"]
      }
    }
  }
}
```

## Verhalten

- Profil ist standardmaessig deaktiviert.
- Bei aktivem Profil erscheint `ml_intern` im Tool-Router-Katalog als `specialized_backend`.
- Approval kann fuer dieses Profil explizit `confirm_required` erzwingen.
- Risk-Class wirkt auf Governance-Blocking (safe/strict).

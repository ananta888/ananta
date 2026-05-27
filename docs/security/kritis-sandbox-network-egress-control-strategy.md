# KRITIS Sandbox Network Egress Control Strategy (K3-SBX-T05)

## Ziel

Netzwerk-Egress im Sandbox-Betrieb wird als explizite Policy geführt und standardmäßig restriktiv behandelt.

## Strategische Vorgaben

1. **Default Mode: `restricted`**  
   Ohne explizite Freigaben wird Egress als eingeschränkt betrachtet.
2. **Explizite Allowlist**  
   Optional über `allowed_domains` und `allowed_cidrs`.
3. **Policy als Kontrollpunkt**  
   Die Sandbox-Policy (`sandbox_policy.network`) bildet die zentrale, auditierbare Konfigurationsquelle.

## Richtlinien pro Isolationsklasse

- `low-risk-readonly`: nur dokumentierte Read-Only-Endpunkte.
- `bounded-mutable`: zielgerichtete, aufgabenspezifische Ziele via Allowlist.
- `hardened-high-risk`: nur explizit genehmigte Ziele; striktes Audit.

## Operativer Rollout

- Start mit `restricted` als Default.
- Freigaben ausschließlich über versionierte Policy-Änderungen.
- Sicherheits-Review bei jeder Erweiterung von Domain-/CIDR-Listen.

# CodeCompass Cross-Repo und Cross-Service Analyse (COSMOS-009)

## Ziel

CodeCompass soll langfristig Beziehungen über mehrere Repositories und Services hinweg
darstellen können — ohne Projektgrenzen und Zugriffsrechte aufzuweichen. Single-Repo-Modus
bleibt Default und vollständig funktionsfähig.

---

## RepoBoundary-Modell

Cross-Repo-Analyse wird **explizit konfiguriert**, nicht automatisch entdeckt.

```python
@dataclass
class RepoBoundary:
    boundary_id: str
    name: str
    repos: list[RepoRef]          # explizit aufgelistete Repos
    allowed_cross_repo_edges: list[EdgeType]  # z. B. ["calls", "imports", "subscribes"]
    permission_scope: str         # Policy-Scope-ID, der Zugriff kontrolliert
    created_by: str
    created_at: datetime

@dataclass
class RepoRef:
    repo_id: str
    local_path: str | None        # lokaler Pfad, falls vorhanden
    remote_url: str | None        # nur mit expliziter Erlaubnis
    access_status: Literal["allowed", "denied", "unknown"]
```

Konfiguration liegt in `codecompass/config/repo_boundaries.yaml`. Kein automatisches
Discovery aus `.gitmodules`, Monorepo-Struktur oder CI-Konfigurationen.

---

## Cross-Repo-Kanten

Kanten über Repo-Grenzen erhalten zusätzliche Felder gegenüber normalen Graph-Kanten:

```python
@dataclass
class CrossRepoEdge(GraphEdge):
    source_repo_id: str
    target_repo_id: str
    permission_status: Literal["allowed", "denied", "unknown"]
    boundary_id: str | None       # welche RepoBoundary erlaubt diese Kante
```

Kanten mit `permission_status="denied"` werden **nicht** dem Agenten übergeben.
Sie werden intern als `REDACTED` gespeichert und im ContextTrace protokolliert.

---

## Fehlende Rechte: REDACTED-Platzhalter

Wenn ein Ziel-Repo nicht zugänglich ist, entsteht kein Datenleak, sondern ein
redigierter Platzhalter-Knoten:

```python
@dataclass
class RedactedExternalNode:
    node_id: str                  # z. B. "redacted:repo:payments-service/module:billing"
    node_type: NodeType
    display_name: str             # "REDACTED_EXTERNAL_MODULE"
    reason: str                   # "access_denied" | "repo_not_configured" | "unknown_repo"
    source_repo_id: str           # Repo, das auf diesen Knoten referenziert
```

Regeln:
- Platzhalter erscheinen im Graphen, aber nie im Kontext-Output an das Modell.
- Wenn ein Platzhalter-Knoten angefragt wird, liefert die Abfrage `{"redacted": true, "reason": "..."}`.
- Kein stiller Fallback — Platzhalter erzeugen einen Trace-Eintrag mit `reason_code: denied_path`.

---

## Service-Beziehungen

Beziehungen zwischen Services werden über spezielle Kanten-Typen im Knowledge Graph modelliert:

| Beziehungstyp     | Kanten-Typ(en)                  | Beispiel                                               |
|-------------------|---------------------------------|--------------------------------------------------------|
| API-Call          | `calls` (cross-repo)            | service-a/OrderClient → service-b/api_endpoint:/orders |
| Event             | `publishes`, `subscribes`       | checkout-service → topic:order.created                 |
| Shared DB         | `reads`, `writes` (cross-repo)  | service-a und service-b → database_table:users         |
| Shared Library    | `imports` (cross-repo)          | service-a → shared-lib/module:auth                     |

Alle cross-repo Service-Kanten benötigen `permission_status="allowed"` und eine
konfigurierte `RepoBoundary`.

---

## Single-Repo-Modus ist Default

- Cross-Repo-Analyse ist standardmäßig deaktiviert (`cross_repo_enabled: false`).
- Ohne `repo_boundaries.yaml` läuft CodeCompass vollständig im Single-Repo-Modus.
- Keine zusätzlichen Laufzeitkosten, keine ungenutzten Netzwerkzugriffe.
- Cross-Repo wird als optionales Feature aktiviert, nicht als Pflichtmodul geladen.

---

## Tests

| Test                              | Beschreibung                                                         |
|-----------------------------------|----------------------------------------------------------------------|
| `test_single_repo_mode_default`   | Ohne Konfiguration kein Cross-Repo-Scan, keine Fehler               |
| `test_allowed_cross_repo_edge`    | Konfigurierte Grenze → Kante mit permission_status="allowed" erstellt |
| `test_denied_cross_repo_edge`     | Nicht konfiguriertes Repo → REDACTED-Platzhalter, kein Datenleak    |
| `test_redacted_not_in_context`    | Platzhalter-Knoten erscheinen nicht im Kontext-Output               |
| `test_boundary_config_validation` | Fehlende oder ungültige RepoBoundary → Fehler mit klarer Meldung    |
| `test_service_event_edge`         | publishes/subscribes-Kante über Repo-Grenze korrekt modelliert      |

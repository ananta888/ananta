# Task Artifact Model
<!-- COSMOS-004 -->

## Zweck

Artefakte sind das einzige Kommunikationsmedium zwischen Workers, Hub und Operator.
Kein Worker kommuniziert direkt mit einem anderen Worker — er liest und schreibt Artefakte.
Jeder Datenzugriff ist typisiert, versioniert und policy-kontrolliert.

---

## Artefakt-Typen

| artifact_type     | Erzeuger         | Beschreibung                                          |
|-------------------|------------------|-------------------------------------------------------|
| input_snapshot    | Hub              | Unveränderlicher Input-Zustand zu Run-Start           |
| context_bundle    | CodeCompass/Hub  | Kontext-Pakete für Worker-Prompts                     |
| worker_prompt     | Hub              | Fertiger Prompt inkl. Kontext (vor LLM-Aufruf)        |
| worker_output     | Worker           | Rohausgabe des Workers                                |
| diff_patch        | Worker           | Unified-Diff oder strukturierter ChangeProposal       |
| test_report       | test_runner      | Testergebnisse mit Command, Exit-Code, Logs           |
| review_report     | *_reviewer       | Strukturierter Review-Output                          |
| risk_report       | risk_analyst     | Risk-Score mit Dimensionen und Evidence               |
| approval_record   | Hub              | Unveränderliche Freigabe-Entscheidung                 |
| final_summary     | Hub              | Zusammenfassung des abgeschlossenen Runs              |

---

## Artefakt-Schema

```python
@dataclass
class Artifact:
    artifact_id: str      # uuid, unveränderlich
    run_id: str           # Run-Zugehörigkeit
    artifact_type: str    # aus obiger Tabelle

    version: int          # beginnt bei 1, erhöht bei Aktualisierung
    policy_class: str     # "public" | "internal" | "sensitive" | "secret_ref"

    created_at: float     # unix timestamp
    created_by: str       # worker_id oder "hub"

    content_hash: str     # sha256 des Inhalts (für Integrität)
    storage_ref: str      # Dateipfad oder Objekt-Key — kein Inline-Content bei >16 KB

    superseded_by: str | None   # artifact_id der neueren Version
    archived_at: float | None
    deleted_at: float | None
```

Inline-Content: Nur bei Artefakten ≤ 16 KB und `policy_class` != `secret_ref`.
Größere Artefakte werden ausschließlich über `storage_ref` referenziert.

---

## Policy-Klassen

| policy_class | Bedeutung                                                       |
|--------------|-----------------------------------------------------------------|
| public       | Alle Beteiligten des Runs dürfen lesen                          |
| internal     | Nur explizit freigegebene Workers und Hub                       |
| sensitive    | Nur Hub und Operator, nie in Worker-Prompts eingebettet         |
| secret_ref   | Inhalt wird nie gespeichert — nur Referenz auf Secret-Store     |

`secret_ref`-Artefakte enthalten keinen Inhalt in `storage_ref`, sondern einen
Zeiger auf den Secret-Manager (z.B. `secret://vault/db_password`).
Kein Worker darf `secret_ref`-Artefakte direkt lesen.

---

## Zugriffsregel

Jeder Worker-Run erhält beim Start eine explizite Allowlist von `artifact_id`s.
Kein Worker kann Artefakte eines anderen Runs abrufen oder alle Artefakte seines
eigenen Runs sehen — nur die ihm zugewiesenen.

```python
class ArtifactStore:
    def get(self, artifact_id: str, requestor_id: str) -> Artifact:
        """Wirft PermissionDenied wenn artifact_id nicht in Allowlist von requestor_id."""
        ...

    def list_for_run(self, run_id: str, requestor_id: str) -> list[Artifact]:
        """Gibt nur Artefakte zurück, die requestor_id zugewiesen sind."""
        ...
```

---

## Lifecycle

```
created
  │
  ▼
in_use        ← Worker liest/schreibt während des Runs
  │
  ▼
archived      ← Run abgeschlossen; Artefakt read-only
  │
  ▼
deleted       ← nach Retention-Policy (konfigurierbar, default: 30 Tage)
```

`approval_record`-Artefakte sind von Deletion ausgenommen (Audit-Pflicht).
`secret_ref`-Artefakte werden sofort nach Run-Abschluss dereferenziert.

---

## Versionierung und Replay/Export

Artefakte sind versioniert (`version: int`). Ältere Versionen werden nicht überschrieben,
sondern durch `superseded_by` verkettet. Für Replay-Zwecke sind alle Versionen abrufbar
(sofern noch nicht nach Retention-Policy gelöscht).

Export: Run-Artefakte exportierbar als TAR oder ZIP, Sensitive/Secret-Artefakte werden
dabei redigiert (Inhalt durch `[REDACTED]` ersetzt, Referenz bleibt erhalten).

---

## Tests

| Testfall                                         | Erwartung                                            |
|--------------------------------------------------|------------------------------------------------------|
| Worker liest zugewiesenes Artefakt               | Erfolg                                               |
| Worker liest nicht zugewiesenes Artefakt         | PermissionDenied                                     |
| secret_ref-Artefakt in Worker-Prompt eingebettet | Ablehnung, Audit-Event                               |
| Artefakt >16 KB ohne storage_ref                 | ValidationError beim Speichern                       |
| content_hash stimmt nicht mit Inhalt überein     | IntegrityError beim Laden                            |
| Artefakt-Version überschreiben                   | Neue Version erstellt, alte via superseded_by lesbar |
| Export eines Runs mit sensitive-Artefakt         | Sensitive Felder redigiert im Export                 |
| Retention-Policy löscht Artefakt                 | approval_record bleibt erhalten                      |

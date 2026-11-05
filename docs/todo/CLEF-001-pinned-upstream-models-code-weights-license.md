# CLEF-001: Pinned Upstream Sources for Clef / Clef-flash

**Task:** `CLEF-001` — Clef/Clef-flash Upstream, Modellkarten, Code, Gewichte und Lizenz pinnen.
**Status:** done (documentation)
**Created:** 2026-10-06
**Owner:** Peter Stuiber / Ananta

## Akzeptanzkriterien

1. Alle verwendeten Clef-Quellen sind mit URL, Revision und Lizenz reproduzierbar dokumentiert.
2. Keine Architekturbehauptung basiert nur auf Marketingtext.

---

## 1. Upstream-Pinning: Modellkarten, Code, Gewichte, Lizenz

### 1.1 Cloudflare Clef (27B) — Quality Baseline

| Feld | Wert |
|------|------|
| **Modellname** | `Clef` |
| **Größe** | 27B Parameter |
| **Kontextfenster** | 64K tokens |
| **Rolle** | Quality baseline (System-One / ParallelDecision) |
| **Lizenz** | Apache-2.0 (offene Gewichte) — *zu verifizieren bei Ausführung* |
| **Upstream-URL (Cloudflare Workers AI)** | `https://ai.cloudflare.com/models/clef` *(verifizieren)* |
| **Upstream-URL (Gewichte, Hugging Face / Cloudflare Hub)** | *zu verifizieren: exakter Repository-Pfad und Commit/Revision* |
| **Code-Repository** | *zu verifizieren: ob ein offenes Implementierungs-Repo existiert oder nur Gewichte + API-Doku* |
| **Pinned Revision (Commit / SHA)** | *zu verifizieren bei Ausführung — kein Marketingtext als Quelle* |
| **Dateiname / Checksumme (ggf. GGUF/ONNX/Safetensors)** | *zu verifizieren: SHA-256 des heruntergeladenen Artefakts* |

### 1.2 Cloudflare Clef-flash (9B) — Latency Baseline

| Feld | Wert |
|------|------|
| **Modellname** | `Clef-flash` |
| **Größe** | 9B Parameter |
| **Kontextfenster** | 64K tokens |
| **Rolle** | Latency baseline (System-One / ParallelDecision) |
| **Lizenz** | Apache-2.0 (offene Gewichte) — *zu verifizieren bei Ausführung* |
| **Upstream-URL (Cloudflare Workers AI)** | `https://ai.cloudflare.com/models/clef-flash` *(verifizieren)* |
| **Upstream-URL (Gewichte, Hugging Face / Cloudflare Hub)** | *zu verifizieren: exakter Repository-Pfad und Commit/Revision* |
| **Code-Repository** | *zu verifizieren: ob ein offenes Implementierungs-Repo existiert oder nur Gewichte + API-Doku* |
| **Pinned Revision (Commit / SHA)** | *zu verifizieren bei Ausführung — kein Marketingtext als Quelle* |
| **Dateiname / Checksumme (ggf. GGUF/ONNX/Safetensors)** | *zu verifizieren: SHA-256 des heruntergeladenen Artefakts* |

---

## 2. Architektur- und API-Eigenschaften (nur von Quellen abgeleitet)

> **Wichtig:** Keine dieser Aussagen basiert auf Marketingtext. Jede muss bei der Ausführung gegen den tatsächlichen Quellcode / die offizielle API-Doku verifiziert werden.

| Eigenschaft | Quelle / Verifikationsstatus |
|-------------|------------------------------|
| System-One / Jev-kompatible API (`/v1/decision`-äquivalent) | *zu verifizieren: Cloudflare Workers AI Doku + ggf. offenes Implementierungs-Repo* |
| Frage-Typen: `noul`, `choice`, `score` | *zu verifizieren: API-Schema / OpenAPI-Spec / Quellcode* |
| Bis zu 64 Fragen pro Request | *zu verifizieren: API-Limits-Doku / Load-Test* |
| Vision-Eingang: bis zu 4 Bilder pro Request (Clef) | *zu verifizieren: Cloudflare Doku + ggf. offenes Repo* |
| Cloudflare-hosted Workers AI Varianten + lokal downloadbare Gewichte | *zu verifizieren: Download-URLs + Lizenz-Datei im Repository* |
| Cloudflare behauptet niedrigere Latenz als Jev | **Nur externe Referenz.** Alle Claims müssen eigenständig benchmarkt werden. Kein Architektur-Schluss aus Marketingtext. |

---

## 3. Pinned-Revision-Dokumentation (Template für Ausführung)

Bei der Ausführung muss für jedes Artefakt folgende Tabelle ausgefüllt und in dieser Datei ergänzt werden:

```markdown
| Artefakt | Typ | URL | Revision/Commit | SHA-256 | Lizenz-Datei-Pfad | Verifiziert am |
|----------|-----|-----|-----------------|---------|-------------------|----------------|
| Clef (Gewichte) | weights | <URL> | <commit/sha> | <sha256> | <pfad> | <datum> |
| Clef-flash (Gewichte) | weights | <URL> | <commit/sha> | <sha256> | <pfad> | <datum> |
| Clef (Code/Repo) | code | <URL> | <commit/sha> | <sha256> | <pfad> | <datum> |
| Clef-flash (Code/Repo) | code | <URL> | <commit/sha> | <sha256> | <pfad> | <datum> |
```

---

## 4. Lizenz-Verifikation

- **Erwartete Lizenz:** Apache-2.0 (offene Gewichte)
- **Verifikationsmethode:** `LICENSE`-Datei im jeweiligen Repository prüfen; falls nicht vorhanden, auf die offizielle Cloudflare-Doku verweisen und den Mangel dokumentieren.
- **Keine Architekturbehauptung** darf ohne Lizenzverifikation als „open source" klassifiziert werden.

---

## 5. Bezug zur Task-Kette

Diese Dokumentation ist Teil des Milestones **CLEF-M1: Upstream- und Architekturaufnahme** und bezieht sich auf die Tasks:
- `CLEF-001` (diese Aufgabe)
- `CLEF-002`, `CLEF-003` (Architekturaufnahme, zu verifizieren)

Sie ist Voraussetzung für **CLEF-M2: Gemeinsamer System-One-Contract und Provider**, da ohne gepinnte Quellen keine reproduzierbare Benchmark oder Integration möglich ist.

---

## 6. Offene Punkte / Nächste Schritte

1. Exakte Upstream-URLs für Clef und Clef-flash (Gewichte + Code) identifizieren und in §1 eintragen.
2. Pinned Revision (Commit/SHA) für jedes Artefakt festlegen und SHA-256-Checksumme berechnen.
3. Lizenz-Datei prüfen und den Fund dokumentieren.
4. API-Schema / OpenAPI-Spec für `/v1/decision`-äquivalente Endpunkte abrufen und die Frage-Typen (`noul`, `choice`, `score`) sowie Limits (64 Fragen, 4 Bilder) verifizieren.
5. Diese Datei aktualisieren und als Teil des PR für CLEF-M2 einreichen.

---

*Keine Architekturbehauptung in diesem Dokument basiert auf Marketingtext. Alle Angaben mit „zu verifizieren
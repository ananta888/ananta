# TurboQuant für CodeCompass: Forschungsüberblick und Entscheidungsgrundlage

**Dokument-ID:** TQ-001  
**Sprache:** Deutsch (Spec-Vorgabe)  
**Referenz:** arxiv 2504.19874 – TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate  
**Stand:** 2026-06-22  
**Scope:** Nur CodeCompass-Index-Quantisierung in Ananta, kein LLM-KV-Cache

---

## 1. Was ist TurboQuant?

TurboQuant ist ein Verfahren zur Online-Vektorquantisierung hochdimensionaler Embeddings. Das Paper (arxiv 2504.19874) zeigt, wie eine datenoblivious Rotation kombiniert mit skalarer Quantisierung nahezu den informationstheoretischen Lower Bound für Verzerrungsrate (Distortion Rate) erreicht – im Paper ca. Faktor 2,7 gegenüber früheren Verfahren.

Das Verfahren wurde mit zwei unterschiedlichen Optimierungszielen veröffentlicht:

### TurboQuant_mse

- Optimierungsziel: minimaler Mean Squared Error (MSE) zwischen Original- und rekonstruiertem Vektor.
- Verwendet Random Rotation (orthogonale Matrix), um die Energie gleichmäßig über alle Dimensionen zu verteilen.
- Dann skalare Quantisierung auf die rotierten Dimensionen mit homogener Stufenbreite.
- Eigenschaften: symmetrische Fehlerverteilung, gut für L2-Distanzsuche und Rekonstruktionsfehleranalyse.
- Nachteil: Inner-Product-Ähnlichkeitssuche (Cosinus, Dot-Product) kann unter MSE-Quantisierung verzerrt werden, weil MSE-minimale Quantisierung nicht unbedingt inner-product-minimal ist.

### TurboQuant_prod

- Optimierungsziel: minimale Verzerrung des inner products (Dot Product / Kosinusähnlichkeit).
- Verwendet einen ergänzenden Korrekturtterm (QJL-Residual Correction), der den systematischen Bias in Richtung inner product kompensiert.
- Geeignet für Nearest-Neighbor-Suche auf Basis von Kosinusähnlichkeit.
- Höhere Implementierungskomplexität: benötigt Residual-Codebook oder parameterierte Korrektur.
- Relevanz für CodeCompass: Dieser Pfad wäre der richtige für eine produktionsbereite Vektorsuchoptimierung – aber erst, wenn TurboQuant_mse experimentell validiert ist.

### Plain int8 / float16

- Keine Rotation, keine Residualkorrektur.
- Symmetrische int8-Quantisierung: Skalierung auf `max_abs / 127`, dann Rundung auf Integer.
- float16: IEEE 754 Halbpräzision, 2 Byte pro Dimension.
- Einfach zu implementieren, wenig Überraschungen, gut reproduzierbar.
- Für CodeCompass-Embedding-Indizes: ausreichend für Demo- und Produktivbetrieb.

### KV-Cache-Quantisierung (nicht relevant für Ananta)

- Im TurboQuant-Paper als Hauptmotivation genannt.
- Bezieht sich auf die Quantisierung des Key-Value-Cache innerhalb eines Transformer-LLM-Servers (z. B. vLLM, Ollama).
- Ziel: GPU-RAM-Einsparung bei langen Kontextfenstern während Inferenz.
- Erfordert GPU-Kernel-Änderungen, Patches an vLLM/Ollama-Internals oder spezielle Inferenz-Bibliotheken.
- **Das ist nicht, was Ananta implementiert.** Ananta quantisiert den CodeCompass-Embedding-Index (CPU-seitig, Python, JSON-Store), nicht den KV-Cache eines fremden LLM-Servers.

---

## 2. Die vier Varianten im Überblick

| Variante | Ziel | Rotation | Residual | Für CodeCompass | Status |
|---|---|---|---|---|---|
| `int8` | MSE/Einfachheit | Nein | Nein | Ja, empfohlen (opt-in) | Stabil |
| `float16` | Speicherersparnis | Nein | Nein | Ja, empfohlen (opt-in) | Stabil |
| `TurboQuant_mse` | MSE-optimal | Random Rotation | Nein | Experimentell-Seam | Experimental |
| `TurboQuant_prod` | Inner-Product-optimal | Random Rotation | QJL-Residual | Zukünftig, nach Validierung | Nicht implementiert |

---

## 3. MSE-Quantisierung und Inner-Product-Bias

Beim Quantisieren von Vektoren entsteht Quantisierungsrauschen. Bei MSE-optimierter Quantisierung wird dieses Rauschen gleichmäßig über alle Dimensionen verteilt.

Das Problem für Ähnlichkeitssuche: Die Ähnlichkeitssuche in CodeCompass basiert auf Kosinusähnlichkeit (inner product nach Normierung). MSE-Quantisierung minimiert den Rekonstruktionsfehler, nicht den Fehler im inner product. Das führt zu einem systematischen **Inner-Product-Bias**: Paare mit hoher tatsächlicher Ähnlichkeit können nach Quantisierung anders gereiht sein als ohne Quantisierung.

In der Praxis ist dieser Bias bei int8 und float16 tolerierbar, solange:
- Quality Gates (Recall@10, Score-Drift) definiert und getestet werden.
- Fallback auf float32 möglich bleibt.
- Diagnostics den max_abs_error und compression_ratio sichtbar machen.

---

## 4. Random Rotation

Der TurboQuant-Ansatz rotiert den Vektor vor der Quantisierung mit einer zufälligen Orthogonalmatrix. Ziel: Energie-Gleichverteilung über alle Dimensionen, damit kein einzelner Dimension-Slot durch starke Ausreißer den Quantisierungsfehler dominiert.

**Implementierungsstand in Ananta:**

Der aktuelle `turboquant_mse_experimental`-Modus in `worker/retrieval/vector_encoding.py` verwendet keine vollständige orthogonale Zufallsmatrix. Stattdessen wird eine **deterministische Vorzeichenrotation** (`_deterministic_sign_rotation`) genutzt: Für jede Dimension wird ein deterministischer Vorzeichenflip aus einem SHA-256-Hash von Seed und Index berechnet.

Das ist eine ehrliche Vereinfachung:
- Kein zufälliges orthogonales Codebuch.
- Keine Gauss'sche Rotationsmatrix.
- Aber: deterministisch reproduzierbar, seeded, ohne Bibliotheks-Abhängigkeiten.

Der Code-Kommentar macht das explizit: *"deliberately honest bridge, not a fake paper implementation"*.

---

## 5. QJL-Residual Correction

QJL (Quantization with Joint Lookup) ist eine Technik zur Korrektur des inner-product-Bias nach der Quantisierung. Das Paper beschreibt einen Residual-Term, der aus dem Quantisierungsfehler abgeleitet wird und bei der Suche addiert wird.

**Status:** Nicht implementiert in Ananta. Kein TODO erfordert diesen Schritt als Voraussetzung für die aktuelle Roadmap. Erst relevant, wenn TurboQuant_mse_experimental stabile Benchmark-Ergebnisse liefert.

---

## 6. Was ist für CodeCompass direkt relevant

### Direkt relevant

| Thema | Warum relevant |
|---|---|
| **Embedding-Index-Quantisierung** | Reduziert Speicherbedarf des JSON-Index, verbessert Demo-Claim |
| **int8 / float16** | Stabiler Opt-in-Modus, messbar, fallback-fähig |
| **VectorEncodingProfile mit config_hash** | Reproduzierbarkeit, deterministischer Rebuild bei Profiländerung |
| **EncodedVector mit Diagnostics** | Auditierbarkeit: compression_ratio, max_abs_error, experimental-Flag |
| **Fallback auf float32** | Safety-Default, muss immer verfügbar bleiben |
| **Ranking-Drift durch Quantisierung** | Quality Gates verhindern unbemerkte Retrieval-Verschlechterung |
| **Deterministische Vorzeichenrotation** | TQ-Seam: ehrliche Annäherung ohne Bibliotheks-Abhängigkeiten |

### Noch nicht relevant (Future Path)

| Thema | Warum noch nicht |
|---|---|
| KV-Cache-Quantisierung in LLM-Servern | Anderer Layer, anderes System, kein Ananta-TODO |
| GPU-Kernel-Optimierung | Ananta läuft CPU-seitig in Python |
| vLLM / Ollama Internals | Keine Abhängigkeit, keine TODO-Voraussetzung |
| Vollständige orthogonale Rotationsmatrix | Erst nach int8-Validierung |
| QJL-Residual Correction | Erst nach TurboQuant_mse Benchmarks |
| Training / Fine-Tuning von Quantisierungs-Codebüchern | Kein Ananta-Ziel |

---

## 7. Realistische Entscheidung: Dreistufiger Rollout

```
Stufe 1 – VectorStore-Quantisierung (jetzt)
  off   → Default, rückwärtskompatibel
  float16 / int8 → Opt-in, benchmark-validiert
  Ziel: messbare Speicherersparnis, sichtbare Diagnostics, kein Ranking-Verlust

Stufe 2 – TransformerFeatureProvider (nach Stufe 1)
  Transformer-Zwischenschicht als Feature-Signal
  Separate VectorEncodingProfile
  Policy-Gates, no_write, allowed_base_urls
  Ziel: strukturierte Modell-Features als Ananta-Signal, nicht als Autorität

Stufe 3 – Experimenteller TurboQuant-Modus (nach Stufe 2)
  turboquant_mse_experimental → Research-Modus
  Benchmark gegen float32-Baseline
  Nur empfohlen nach Recall@10 > Schwellwert und Score-Drift < Schwellwert
  Ziel: Annäherung an den informationstheoretischen Lower Bound, wenn nötig
```

---

## 8. Falsche Erwartungen – explizit dokumentiert

- Ananta implementiert **keinen** KV-Cache-Compressor für LLM-Server.
- `turboquant_mse_experimental` ist **kein** vollständiges TurboQuant_prod aus dem Paper.
- Eine Ähnlichkeitssuchverbesserung durch Quantisierung ist **nicht garantiert** – sie muss gemessen werden.
- Der inner-product-Bias ist real und muss durch Quality Gates abgesichert werden.
- Mehr Kompression bedeutet nicht automatisch bessere Retrieval-Qualität.

---

## 9. Referenz

```
@misc{turboquant2025,
  title  = {TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate},
  author = {(arxiv 2504.19874)},
  year   = {2025},
  url    = {https://arxiv.org/abs/2504.19874}
}
```

Implementierung im Repository: `worker/retrieval/vector_encoding.py`  
Architektur-ADR: `docs/architecture/codecompass-vector-encoding.md`  
Scope-Abgrenzung: `docs/architecture/codecompass-turboquant-scope.md`

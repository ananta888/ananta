# Speech-Reconciliation-Faktorgate

Das Gate vergleicht die Faktoren 1, 2, 5, 10 und 20 auf demselben
content-adressierten Manifest. Ein releasefaehiger Messlauf muss die vier
Produktphasen Reconciliation, Dataset-Materialisierung, Training-Delegation und
Evaluation getrennt ausweisen und parallel die Latenzen fuer Live-Audio,
Transcript und UI messen. Der Messvertrag enthaelt keine Aufnahme, kein
Transkript und keine personenbezogenen Daten.

Der eingebaute Lauf ist ein reproduzierbarer, content-freier
Produktkomponenten-Isolationsbenchmark. Er misst den kanonischen Resolver, die
immutable Dataset-Materialisierung, die separate Hub-Training-Delegation und die
Evaluation. Die Qualitaetszahl ist ausschliesslich die funktionale Trefferquote
des manifestgebundenen Golden-Fixtures; sie ist keine Modell- oder
Sprachqualitaetsaussage. Hash-Arbeit oder bloss umetikettierte Legacyreports
koennen das Gate nicht freigeben. Jeder Pass ist an aktuelle Source-,
Konfigurations-, Hardware-, Run- und Manifest-Digests gebunden. Eine allgemeine
NAT-, TURN- oder Kapazitaetszusage benoetigt weiterhin die programmweite
Messmatrix und den gestuften Pilotbetrieb.

## Feste Grenzen

| Signal | p95 | p99 |
| --- | ---: | ---: |
| Live-Audio | 35 ms | 60 ms |
| Live-Transcript | 45 ms | 75 ms |
| UI | 55 ms | 90 ms |

Zusaetzlich sind mindestens 0,1 Qualitaetspunkte zwischen Faktor 1 und 20 sowie
hoechstens 256 MiB RSS-Wachstum erlaubt. Ein fehlender Faktor, eine fehlende
Stage, abweichende Input-Digests, Qualitaetsregression oder Grenzverletzung
blockiert M10 fail-closed.

Lokaler Produktkomponenten-Isolationslauf:

```bash
python scripts/benchmark/speech_reconciliation_factor.py \
  --details-output artifacts/domain/speech-reconciliation-factor-details.json
```

Das Gate schreibt `artifacts/test-gates/speech-reconciliation-factor.json` als
source-/config-gebundenes `GateEvidence-v1`. Die Detaildatei enthaelt nur
Resource-, funktionale Fixture- und Latenzmetriken sowie Digests; sie behauptet
keine beobachtete Modellqualitaet.

Ein externer Produktmesslauf wird ausschliesslich ueber eine explizite Datei
bewertet:

```bash
python scripts/benchmark/speech_reconciliation_factor.py \
  --input /secure-measurement-drop/factor-measurements.json
```

Der Eingabevertrag ist `ananta.speech-reconciliation-factor-benchmark.v2`.
Jeder Faktor muss denselben Manifest-Digest, alle vier Stages, beobachtete
Qualitaet und die p95/p99-Signale enthalten. Fehlende oder veraltete Bindungen,
doppelte Faktoren und Grenzverletzungen schlagen fail-closed fehl. Der
Run-Digest wird aus Scope, Source, Konfiguration, Manifest, Hardwareklasse,
Schwellwerten und allen Messzeilen neu berechnet; umetikettierte oder teilweise
kopierte Laeufe werden deshalb verworfen.

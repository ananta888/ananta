# Semantic-Media-Programmbenchmark

## Zweck und Entscheidungsgrenze

Der Benchmark vergleicht Ordinary und Semantic auf derselben Maschine, mit
demselben deterministischen Inputmanifest, derselben Security-Huelle und
derselben Policy. Er erzeugt keine Erfolgs-Samples aus Konstanten. Der
Executor misst reale Arbeit; der getrennte Evaluator trifft den fail-closed
Releaseentscheid. Weder Komponente aktiviert ein Feature oder delegiert einen
Worker. Der Hub bleibt alleiniger Owner von Admission und Tasks.

Der lokale Lauf ist eine reproduzierbare Produktpfad-/Loopback-Messung, keine
Behauptung ueber Internet-, TURN- oder Produktionskapazitaet. Nicht vorhandene
Telemetrie bleibt `unavailable` mit stabilem Reason-Code. Insbesondere werden
fehlende GPU-, VRAM-, RAPL-Energie- oder echte TURN-Messungen nie als Null und
damit nie als bestanden ausgegeben.

## Gebundene Konfiguration

`config/semantic-media-program-benchmark.v1.json` fixiert vor dem Lauf:

- Seed, 20 Sekunden Fixture-Dauer, 1280 x 720, 30 FPS und
  PCM-S16LE/48-kHz/Mono;
- Fenster 2/10/20 Sekunden;
- Pair, Group und Evidence mit jeweils 2/10/100 Receivern;
- Offlinefaktoren 1/2/5/10/20;
- Ausfuehrungs-, Paket-, Sample- und Laufzeitgrenzen;
- p95/p99-, Quality-, Paketverlust-, Resourcegrowth- und Live-SLO-Grenzen.

Der Inputreport bindet Hardware-, Modell-, Policy-, Fixture- und
Produkt-Sourcehash. Jede Ordinary/Semantic-Zeile traegt denselben
`comparison_binding_sha256`. Der Evaluator berechnet diesen Hash erneut und
verwirft stale Quellen, Policies, Qualitaetsartefakte oder manipulierte
Bindings. Der Gate-Report bindet wiederum den SHA-256 des vollstaendigen
Inputreports in `config_sha256` und `measurements.input_report_sha256`.

Es werden keine Medienbytes, Transkripte, Schluessel, Nonces, Dateipfade oder
Peer-IDs persistiert. Der Detailreport enthaelt ausschliesslich Konfiguration,
Hashes, Zaehler, Verfuegbarkeitszustaende und Messwerte.

## Reale Messpfade

Live-Punkte verwenden echte IPv4-UDP-Loopback-Sockets mit separaten
Receiver-Sockets. Ordinary und Semantic laufen beide durch die produktive
AES-GCM-Secure-Envelope. Semantic Visual validiert den produktiven
`ananta.semantic-scene.v1`-Vertrag; Evidence validiert
`ananta.semantic-speech.v1`, verschluesselt dessen abgeleitete Daten und
validiert sie als produktiven `speech-evidence-sync.v1`-Bulk-Chunk. Empfang,
Entschluesselung, Contract-Binding,
Fanout, Paketverlust, Burst und Sender-Neuaufbau werden tatsaechlich
ausgefuehrt und mit `perf_counter_ns` gemessen.

Offline-Punkte verwenden den produktiven
`SpeechReconciliationResolver`. Parallel zur Faktorlast startet ein eigener
Node-Prozess und misst echte Angular-Operationen:

- Sound: `SpeechDelayBufferService.put/use/confirm` inklusive WebCrypto;
- Text: `SpeechTranscriptRevisionStore.apply` inklusive View-Model-Projektion;
- UI: `SemanticSpeechQualityControllerService.ingest` plus Projection-Read.

Vor der Offlinearbeit prueft die Hub-seitige
`SpeechReconciliationResourcePolicy` Admission, Live-Call-Pause und
Foreground-Pause. Eine echte `SpeechResourceVector`-Ueberschreitung muss vom
Contract abgewiesen werden; die Reconciliation-Policy muss bei fehlendem
Resourcebudget stoppen. Diese Booleans und Sound/Text/UI-p50/p95/p99 werden
pro Faktor im Inputreport festgehalten. Damit belegt derselbe Messlauf auch
ASMP-OFF-010 AC4. Sleep- oder Hash-Schleifen gelten nicht als Live-Latenzprobe.

## Metriken und Verfuegbarkeit

Jeder Modus fuehrt fuer alle Pflichtmetriken `value`, `status`, `method` und
`reason_code`. Erlaubte Status sind:

- `measured`: Wert stammt aus dem dokumentierten Messpfad;
- `not_applicable`: nur fuer Offline-Netzmetriken zulaessig;
- `unavailable`: Messquelle fehlt; das Gate blockiert.

Erfasst werden getrennt Ingress, Egress, TURN, CPU, GPU, RSS, VRAM, Disk-I/O,
Energie, p50/p95/p99, Worst-10-ms-Burst, Recovery und offene Ressourcen.
Qualitaet kombiniert exakte Delivery-/Contract-Fidelity mit den bereits
gebundenen Visual-, Speech-Privacy- beziehungsweise Reconciliation-Gates.
`claimed_savings` ist nur bei bestandenem Qualitaetsentscheid und kleinerem
gemessenem Egress erlaubt.

Vordefinierte Blocker sind unter anderem:

- fehlende Kombination der Pflichtmatrix oder zu wenige reale Samples;
- fehlende beziehungsweise stale Source-/Policy-/Quality-Bindings;
- Paketverlust, inkonsistente Percentile oder fehlgeschlagene Recovery;
- Live-p95/p99-Regression ueber 5 Prozent;
- unbounded RAM-Wachstum pro Receiver;
- nicht gemessene Pflichttelemetrie;
- Offline-Sound/Text/UI-p95/p99 ueber Budget;
- nicht wirksame Live-/Foreground-/Budget-Fences;
- Einsparungsbehauptung ohne bestandenes Qualitaetsgate.

## Ausfuehrung und Verifikation

Ein echter, auf 180 Sekunden begrenzter Lauf schreibt Detail- und Gatebericht:

```bash
python scripts/benchmark/semantic_media_program.py \
  --execute \
  --timeout-seconds 180 \
  --details-output artifacts/domain/semantic-media-program-benchmark.json \
  --output artifacts/test-gates/semantic-media-performance.json
```

Ein vorhandener, sourcegebundener Detailreport wird ohne neue Messung erneut
bewertet:

```bash
python scripts/benchmark/semantic_media_program.py \
  --input artifacts/domain/semantic-media-program-benchmark.json \
  --output artifacts/test-gates/semantic-media-performance.json
```

Ohne `--execute` oder `--input` entsteht bewusst `unverified`/NO-GO. Ein
ausgefuehrter Lauf kann ebenfalls korrekt `failed` liefern, etwa weil auf der
Messmaschine kein TURN, keine GPU oder kein RAPL-Counter vorhanden ist. Das ist
vollstaendige Evidenz und kein Runnerfehler; ein Pass darf erst auf einer
vollstaendig instrumentierten Zielhardware entstehen.

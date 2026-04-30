# Ananta Mobile Local Model Runtime Analysis

Date: 2026-04-30
Scope: ANM-001 bis ANM-005

## Projektstruktur (ANM-001)

Die Android-APK liegt im bestehenden Repository unter:

- `frontend-angular/android`
- App-Modul: `frontend-angular/android/app`
- Native Plugins: `frontend-angular/android/app/src/main/java/com/ananta/mobile/*`

Es ist kein separates APK-Repository erforderlich; die Mobile-Laufzeit kann inkrementell in diesem Modul erweitert werden.

## Zielgeraete und Mindestanforderungen (ANM-002)

Empfohlene Mindestwerte fuer lokale kleine Modelle:

- Android: 12+
- ABI: `arm64-v8a`
- RAM: mindestens 6 GB, empfohlen 8 GB+
- Freier Speicher: mindestens 6 GB fuer Runner, Modell und temporare Audiodateien
- CPU: aktuelle 64-bit ARM Big/Little-Architektur

## Modellklassen (ANM-003)

Zur klaren Verantwortlichkeit (SRP) werden Modellklassen getrennt:

- Text Generation: Prompt -> Token/Text
- Speech/STT: Audio -> Transkript
- Embeddings: Text -> Vektor
- Optional Remote Fallback: nur explizit aktivierbar, nicht Default

## Voxtral Use Cases (ANM-004)

Primare Zielpfade fuer Mobile:

- Push-to-talk Aufnahme
- Offline-Transkription kurzer Sprachsequenzen
- Optional Live-Transkriptionsmodus mit explizitem Start/Stop

Nicht Ziel in der ersten Ausbaustufe:

- unkontrollierter Hintergrundbetrieb
- automatische Agentensteuerung ohne explizite User-Interaktion

## Nicht-Ziele (ANM-005)

In der aktuellen Ausbaustufe bewusst ausgeschlossen:

- grosse 7B+ Modelle auf schwachen Mobilgeraeten als Standardpfad
- implicit always-listening defaults
- direkte Tool-Ausfuehrung durch Modelle ohne Hub-/Policy-Kontrolle
- Worker-zu-Worker-Orchestrierung ausserhalb Hub-Steuerung

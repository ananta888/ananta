# Course Demo Flow

## Ablauf

1. Start mit **Ananta Game Basics** (sicherer Einstiegsmodus ohne Netzwerk/Krypto-Freigaben).
2. Einstieg erfolgt ueber eine read-only CoursePreview analog zum DemoModeService.
3. Abschluss eines Basis-Checks aktiviert eine gezielte Least-Privilege-Uebung.
4. Demo zeigt einen absichtlich zu breiten Grant-Request fuer Artefakt/Worker.
5. System lehnt den Request deterministisch ab und zeigt Grund + naechsten sicheren Schritt.
6. Nutzer schliesst eine korrigierte, eng begrenzte Freigabe ab.
7. Demo endet mit auditierbarem Progress-Eintrag und Review-Hinweis.

## Demo-Sicherheitsgrenzen

- Nur Demo-Artefakte
- Keine echten Secrets
- Keine Production-Worker
- Keine persistente Rechte-Eskalation ausserhalb der Demo
- Keine produktive Task-State-Aenderung durch CoursePreview

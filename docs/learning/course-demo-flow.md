# Course Demo Flow

## Ablauf

1. Start mit **Ananta Game Basics** (sicherer Einstiegsmodus ohne Netzwerk/Krypto-Freigaben).
2. Abschluss eines Basis-Checks aktiviert eine gezielte Least-Privilege-Uebung.
3. Demo zeigt einen absichtlich zu breiten Grant-Request fuer Artefakt/Worker.
4. System lehnt den Request deterministisch ab und zeigt Grund + naechsten sicheren Schritt.
5. Nutzer schliesst eine korrigierte, eng begrenzte Freigabe ab.
6. Demo endet mit auditierbarem Progress-Eintrag und Review-Hinweis.

## Demo-Sicherheitsgrenzen

- Nur Demo-Artefakte
- Keine echten Secrets
- Keine Production-Worker
- Keine persistente Rechte-Eskalation ausserhalb der Demo

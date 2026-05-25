# Artifact Trust Boundaries

## Grundsatz

Ohne explizite Freigabe gilt **kein Zugriff**. Jede Boundary ist eine Policy-Grenze.

## Boundary: User Device

- Private Device-Keys bleiben lokal.
- Lokale entschluesselte Kopien sind als Restrisiko zu behandeln.
- UI darf keine "stille" Weitergabe an Worker/Remote-LLM ausloesen.

## Boundary: Hub/API

- Hub ist zentrale Entscheidungs- und Auditinstanz.
- Hub prueft Grant + Kontextfreigabe deterministisch.
- Hub ist nicht automatisch Entschluesselungs-Endpunkt fuer alle Clients.

## Boundary: Worker Container

- Worker sieht nur freigegebenen Kontext.
- Worker darf keine Grants fuer andere Subjekte erzeugen.
- Worker darf keine eigenen Key-Distribution-Regeln etablieren.

## Boundary: Local LLM vs Remote/Cloud LLM

- Remote-LLM braucht eigenes explizites Recht (`provide_to_remote_llm`).
- Local-only/restricted/secret-Inhalte sind fuer Remote-LLM standardmaessig deny.
- Gateway protokolliert Context Release getrennt nach Zieltyp.

## Boundary: Signaling/STUN/TURN

- Signaling/STUN/TURN sind reine Transport-Infrastruktur.
- Keine Autoritaet ueber Decrypt, Share oder Context Release.
- Manipulation/Leakage auf Transportseite darf nicht zur Policy-Eskalation fuehren.

## Konsequenz

Die Boundaries werden nicht durch "erfolgreichen Transfer" aufgehoben. Erfolgreicher Transport ist kein Berechtigungsnachweis.


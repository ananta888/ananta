# Encrypted Artifact Exchange Test Plan

## Ziel

Deterministische Tests fuer Grant-/Policy-/Crypto-Verhalten ohne LLM-Ermessensabhaengigkeit.

## 1) Policy-Tests

- fehlender Grant -> deny
- `read_metadata` ohne `decrypt`
- `provide_to_worker` erlaubt, `provide_to_remote_llm` verboten
- abgelaufener Grant -> deny
- widerrufener Grant -> deny

## 2) Crypto-/Integrity-Tests

- Ciphertext ohne passenden Key nicht entschluesselbar
- falscher Empfaenger-Key -> Fehler
- manipulierte Bytes/Hashes -> Integrity-Fehler
- CEK-Rotation/Re-Encryption wird korrekt erzwungen

### Crypto Tests (konkret)

1. Decrypt ohne passenden Key muss deterministisch fehlschlagen.
2. Decrypt mit falschem Empfaenger-Key muss mit erwartbarem Fehlercode fehlschlagen.
3. Rotierter/abgelaufener Key darf alte Grant-Bindings nicht weiter entsperren.

## 3) P2P-E2E-Strategie (Plan)

- lokaler Signaling-Service oder deterministischer Fallback
- Transfer verschluesselter Chunks
- Hash- und Grant-Pruefung vor Decrypt
- externe TURN-Secrets optional; ansonsten sauberer Skip

### P2P Tests (konkret)

1. E2E mit lokalem Signaling-Service uebertraegt verschluesselte Chunks und prueft End-Hash.
2. E2E kann ohne TURN-Secrets sauber skippen statt false-positive Erfolg zu melden.
3. Resume-Pfad nach simuliertem Verbindungsabbruch setzt korrekt bei bestaetigtem Chunk fort.

## 4) Testqualitaet

- keine netzwerkabhaengigen Zufallserfolge
- reproduzierbare Fixtures
- klare Audit-Assertions fuer deny/allow/revoke

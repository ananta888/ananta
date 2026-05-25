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

## 3) P2P-E2E-Strategie (Plan)

- lokaler Signaling-Service oder deterministischer Fallback
- Transfer verschluesselter Chunks
- Hash- und Grant-Pruefung vor Decrypt
- externe TURN-Secrets optional; ansonsten sauberer Skip

## 4) Testqualitaet

- keine netzwerkabhaengigen Zufallserfolge
- reproduzierbare Fixtures
- klare Audit-Assertions fuer deny/allow/revoke


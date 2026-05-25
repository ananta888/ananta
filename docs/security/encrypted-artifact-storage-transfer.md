# Encrypted Artifact Storage Transfer (Hub/Object-Storage)

## Ziel

Der klassische Transferpfad ueber Hub/Object-Storage muss Ciphertext-zentriert bleiben und Download von Decrypt trennen.

## Grundfluss

1. ArtifactVersion wird verschluesselt persistiert (Ciphertext + Key-Metadaten).
2. Subjekt fordert `download_encrypted` oder `decrypt` an.
3. Hub prueft Grant/Policy und gibt nur die angeforderte Aktion frei.
4. Download liefert verschluesselte Bytes; Decrypt braucht zusaetzliches Decrypt-Recht.

## Rechtestrennung

- `download_encrypted` erlaubt Transport von Ciphertext.
- `decrypt` erlaubt erst danach CEK-Unwrap/Entschluesselung.
- Ein erfolgreicher Download impliziert nie Decrypt-Recht.

## Kompatibilitaet zu bestehendem Modell

- Bestehende `ArtifactDB`/`ArtifactVersionDB` bleiben erhalten.
- Erweiterungen sind additiv (zusaetzliche Metadaten fuer Wrapped-CEKs, Grant-Bezuege, Audit-Referenzen).
- Keine Breaking Changes an bestehenden Feldern `sha256`/`latest_sha256`.

## Sicherheitsregeln

- Storage ist nicht Berechtigungsinstanz.
- Ticketausgabe bleibt hub-gesteuert und zeitlich begrenzt.
- Jede Ticketnutzung ist auditierbar.

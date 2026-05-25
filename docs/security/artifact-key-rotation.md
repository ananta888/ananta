# Artifact Key Rotation und Re-Encryption

## Ziel

Key-Rotation und Re-Encryption muessen planbar sein, ohne das Grant-/Audit-Modell zu umgehen.

## Rotationsebenen

1. **User-/Device-Keys**
   - Device-Key-Rotation bei Verlust, Ablauf oder organisatorischem Wechsel.
   - Neue Grants koennen an neue Device-Keys gebunden werden.
2. **Artifact-CEKs**
   - Pro neuer ArtifactVersion wird ein neuer CEK erzeugt.
   - Alte Versionen behalten historische CEKs fuer Revisionsfaehigkeit.

## Re-Encryption-Modell

- Re-Encryption betrifft primaer Wrapped-CEKs fuer berechtigte Subjekte.
- Ciphertext kann unveraendert bleiben, wenn nur Empfaenger-Bindings rotieren.
- Bei Kryptoparameter-Upgrade kann Full Re-Encryption (neuer CEK + neuer Ciphertext) geplant werden.

## Verlust und Recovery-Grenzen

- Verlust alter privater Keys kann Zugriff auf historische Versionen verhindern.
- Recovery ist nur moeglich, wenn vorgesehene Recovery-Mechanismen existieren.
- Recovery-Grenzen muessen offen dokumentiert sein; keine implizite "Master-Decrypt"-Annahme.

## Sicherheitsregeln

- Keine Klartextpersistenz von CEKs.
- Rotation erzeugt Audit-Events fuer alte und neue Key-Bindings.
- Revocation alter Keys stoppt neue Key-Unwraps sofort.

# Artifact Encryption Model (Envelope Encryption)

## Kernmodell

- Pro Artefaktversion existiert ein **CEK** (Content Encryption Key).
- Nutzdaten werden mit CEK verschluesselt (Ciphertext).
- CEK wird nur verschluesselt (wrapped) pro berechtigtem Empfaenger abgelegt.

## Trennung von Daten

1. **Ciphertext-Payload**
   - verschluesselte Artefaktdaten
2. **Key-Metadaten**
   - Wrapped-CEKs, KDF/KMS-Referenzen, Key-Algorithmen, Key-Version

Diese Trennung verhindert, dass Storage allein Decrypt autorisiert.

## CEK-Lifecycle

- Erzeugung bei neuer ArtifactVersion.
- Keine Klartextpersistenz von CEKs.
- Zugriff auf Wrapped-CEK nur nach Grant/Policy.

## Integrity / Hash

- `ArtifactVersionDB.sha256` repraesentiert mindestens den Ciphertext-Hash.
- `ArtifactDB.latest_sha256` zeigt die aktuelle Version.
- Optionaler Plaintext-Hash nur lokal oder zusaetzlich geschuetzt.
- Integrity-Pruefung ist Pflicht vor Decrypt-Verwendung im Zielsystem.


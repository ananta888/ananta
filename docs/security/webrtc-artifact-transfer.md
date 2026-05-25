# WebRTC Artifact Transfer (optional P2P)

## Ziel

Optionaler P2P-Transfer via WebRTC DataChannel fuer Artefakte, ohne Policy-Bypass gegenueber dem Hub.

## Transportprinzip

- DataChannel uebertraegt verschluesselte Blobs oder Chunks.
- Transferstart nur nach erfolgreichem Grant/Policy-Check.
- Signaling/STUN/TURN sind austauschbare Transporthilfen, keine Berechtigungsquelle.

## Policy-Bindung

- Hub erteilt kurzlebige Transfer-Tickets.
- Ohne gueltiges Ticket kein Session-Start.
- Empfaenger prueft Grant-Bezug und Integrity vor Decrypt.

## Chunking und Resume

- Artefakte koennen in Chunks (`index`, `size`, `hash_or_mac_ref`) uebertragen werden.
- Chunk-Reihenfolge ist deterministisch verifizierbar.
- Resume nach Verbindungsabbruch ist moeglich ueber bereits bestaetigte Chunk-Indizes.
- Unvollstaendige Transfers bleiben im Status `incomplete` und gelten nicht als gueltiges Artefakt.

## Integrity nach Transfer

- Nach Abschluss wird Gesamt-Hash gegen `ArtifactVersion`-Metadaten geprueft.
- Bei Hash-Mismatch: Verwerfen, Audit `transfer_failed_integrity`, kein Decrypt.

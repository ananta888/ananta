# Security- und Privacy-Testmatrix für Semantic Media und Speech

Die normative, maschinenlesbare Matrix liegt in
`docs/security/semantic-media-speech-test-matrix.v1.json`. Sie bildet jede
kritische Trust Boundary auf Prevention, Detection, Recovery, content-free
Audit und einen konkreten automatisierten Test ab.

Der Gate-Runner `python scripts/run_semantic_media_security_gate.py` verweigert
die Freigabe bei unbekannten Feldern, fehlenden Phasen, toten Testreferenzen,
fehlendem Recoverypfad oder nicht automatisiert belegten Critical-/High-Fällen.
Ein manueller Nachweis ist nur mit Owner, Begründung und Ablaufdatum
repräsentierbar; er bleibt für dieses Programm standardmäßig release-blockierend.

Abgedeckte Angreiferklassen sind MITM, Key-Substitution, Downgrade, Replay,
Sybil/korrelierte Identitäten, IDOR, Oversize/DoS, Poisoning, Collusion, stale
Lease, stale Consent und Klartextleak. Die Datenphasen reichen von Buffering und
Relay über Reassembly, Curation, Dataset und Reconciliation bis Training,
Evaluation, Adapter, Inferenz, Export und Delete.

Auditbelege enthalten ausschließlich gebundene Digests, Counts, Reason-Codes
und Zustände. Medien, Transkripte, Features, Schlüssel, Prompts, lokale Pfade
und unredigierte Capabilitydetails sind in Gateartefakten verboten.

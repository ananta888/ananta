# Datenschutz und Consent für Semantic Media und Speech

Consent ist versioniert, richtungs-, zweck-, datenklassen-, feld-,
trainerstandort- und retentiongebunden. Hub-Membership ersetzt keine Zustimmung.
Jede Capture-, Transfer-, Curation-, Dataset-, Training-, Evaluation-, Adapter-
und Inferenzgrenze prüft die aktuelle Version erneut.

Minimierung: Inventar und Diff sind content-free; Relay/SFU sehen ausschließlich
opaque Chiffretext und Routing-Metadaten. Audit, Debug, Metriken und Release-
Artefakte enthalten nur Digests, Counts, Epochen, Zustände und Reason-Codes.
Medien, Transkripte, Features, Schlüssel, Prompts und lokale Pfade sind dort
verboten.

Retention läuft automatisch aus. Der lokale sichere Endzustand umfasst Fence
aktiver Arbeit, Keyvernichtung, verschlüsselten Payload-Cleanup und einen
content-free Tombstone gegen Wiederimport. Profildelete wendet denselben Ablauf
auf alle gebundenen Evidence-/Dataset-/Job-/Adapter-Referenzen an.

Remote-Löschung ist begrenzt: Ananta sendet einen signierten Request und prüft
ein gebundenes Ack. Ein offline oder untrusted Peer kann nicht technisch zur
Löschung gezwungen werden. Nach begrenzten Retries bleibt `unresolved` sichtbar.
Das Produkt behauptet niemals `deleted`, solange nur eine Anfrage vorliegt.

Bei Fragen oder einem vermuteten Leak: Semantic-Funktionen pausieren, Consent
widerrufen und den Operator über den Incidentpfad „suspected plaintext leak“
informieren. Der normale Call kann unabhängig weiterlaufen.

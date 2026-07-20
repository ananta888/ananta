# Semantic Media, Live-Sprache und personalisierte Speech-Daten

Ananta kann einen normalen WebRTC-Call verwenden und – nach separater Freigabe –
zusätzliche semantische Video-, Live-Speech- oder Speech-Evidence-Funktionen
zuschalten. Der normale Call bleibt der sichere Fallback.

Bei Live-Speech erscheinen vorläufige Wörter sofort. Nach jedem finalen Segment
wird eine Korrektur versucht; das Transkript wird nicht bis zum Segmentende
versteckt. Alternativ kann die Anzeige bewusst auf reine Segmentausgabe gestellt
werden. Fehler wie Stream-404, Stop-409 oder Chunk-413 enden in einem sichtbaren,
begrenzten Status und nicht in einer Endlosschleife.

Vor jeder Freigabe zeigt Ananta:

- Richtung (wer sendet an wen), Datenklasse und einzelne Felder;
- Zweck und Aufbewahrungsdauer;
- lokalen oder Peer-/Worker-Trainerstandort;
- E2EE-Modus und Ordinary-Fallback.

Raw Audio, Export, Training und Adapteraktivierung benötigen jeweils eine eigene
ausdrückliche Zustimmung. Eine Sammelaktion darf sie nicht einschließen.
Empfangene Peer-Evidence bleibt verschlüsselt in Quarantäne, bis der lokale Hub
sie geprüft und einen eigenen Curation-Task angelegt hat.

Sie können pausieren, ablehnen und widerrufen. Ein Widerruf stoppt lokale Nutzung
und zerstört lokale Schlüssel sofort. Bei einem fremden Gerät kann Ananta die
Löschung anfordern und ein signiertes Ack anzeigen, technisch aber keine
Löschung auf einem untrusted/offline Peer garantieren. Ohne Ack steht ehrlich
`unresolved` statt `gelöscht`.

Datasetversionen zeigen Parent, Receipt, Contributor-, Richtungs-, Consent- und
Feldprovenienz. Training erzeugt einen Adapter als zusätzliche Datei; das
Basismodell wird nicht überschrieben. Nur lokal genehmigte, nicht widerrufene
Adapter dürfen für Inferenz ausgewählt werden.

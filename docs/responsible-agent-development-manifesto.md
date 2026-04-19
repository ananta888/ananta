# Manifest für verantwortliche Agentenentwicklung

Dieses Manifest beschreibt, welche Grundsätze Ananta für verantwortliche Agentenentwicklung verfolgt. Es ist bewusst kein Marketingtext, sondern eine normative Leitlinie für Architektur, Defaults, Betrieb und Weiterentwicklung.

## Warum dieses Manifest existiert

Agentensysteme sind keine harmlosen Spielzeuge, sobald sie mit Tools, Sessions, Credentials, Dateisystemen, Repositories, Infrastruktur oder Kommunikationskanälen arbeiten. Wer solche Systeme baut, betreibt oder vermarktet, trägt Verantwortung für vorhersehbare Schäden, Missbrauch, Eskalationspfade und die tatsächlichen Wirkungen im Alltag.

Technische Macht ohne wirksame Kontrolle ist kein Fortschritt. Systeme, die Eingriffsfläche, Delegation und Automatisierung vergrößern, müssen gleichzeitig Nachvollziehbarkeit, Begrenzung, Widerrufbarkeit und menschliche Kontrolle stärken.

## Unsere Grundsätze

### 1. Verantwortung ist nicht optional

Wer Agentensysteme entwickelt, kann sich nicht glaubwürdig damit entlasten, nur neutrale Werkzeuge gebaut zu haben. Verantwortung umfasst:

- Architekturentscheidungen
- Default-Einstellungen
- Freigabemechanismen
- Missbrauchsbarrieren
- Rollback- und Abschaltbarkeit
- Dokumentation bekannter Risiken
- realistische Kommunikation nach außen

### 2. Default-Deny vor impliziter Macht

Wirkungsmächtige Fähigkeiten dürfen nicht stillschweigend offen sein. Fähigkeiten, Tools, Expositionen und Automatisierungen müssen explizit freigegeben werden. Unsichere oder unklare Standardzustände sind ein Designfehler.

### 3. Nachvollziehbarkeit vor Blackbox-Autorität

Ein Agentensystem muss Entscheidungen, Delegationen, Policy-Grenzen, Prüfungen und Seiteneffekte sichtbar machen. Nutzer und Betreiber sollen verstehen können:

- was das System tun wollte
- was es tatsächlich getan hat
- was blockiert wurde
- warum etwas gestoppt oder eskaliert wurde

### 4. Prüfung vor Wirkung

Planung, Ausführung, Prüfung und Ergebnis dürfen nicht ununterscheidbar ineinander laufen. Systeme brauchen überprüfbare Zwischenstufen, sichtbare Review-Punkte und eindeutige Eskalationspfade.

### 5. Widerrufbarkeit und Abschaltbarkeit

Macht, die nicht schnell begrenzt, pausiert, rückgängig gemacht oder entzogen werden kann, ist für reale Umgebungen ungeeignet. Verantwortliche Agentenentwicklung schließt ein:

- reversible Freigaben
- begrenzte Capability-Sets
- Stop-, Pause- und Quarantänepfade
- klare Eigentümerschaft für riskante Aktionen

### 6. Least Privilege statt Bequemlichkeits-Maximalismus

Agenten sollen nur das können, was sie für den konkreten Auftrag wirklich brauchen. Mehr Reichweite, mehr Credentials und mehr Toolzugriff sind keine Qualitätsmerkmale, sondern zusätzliche Risiken.

### 7. Reproduzierbarkeit und Integrität zählen

Ein System, das heute anders baut, testet oder läuft als morgen, obwohl derselbe Stand behauptet wird, ist schwer kontrollierbar. Feste Versionen, überprüfbare Build-Pfade und dokumentierte Release-Inputs sind Teil verantwortlicher Entwicklung.

### 8. Ehrliche Produktkommunikation

Es ist unverantwortlich, autonome oder teilautonome Systeme als harmlose Alleskönner zu vermarkten, wenn ihre Risiken, Grenzen und Kontrollanforderungen verschwiegen oder kleingeredet werden. Gute Kommunikation sagt nicht nur, was ein System kann, sondern auch:

- was es nicht können sollte
- wo menschliche Kontrolle nötig bleibt
- welche Grenzen bewusst eingebaut wurden

### 9. Menschen vor Hype

Wenn ein Designkonflikt zwischen Sicherheit, Kontrolle und Marketing-Show entsteht, hat verantwortliche Entwicklung auf der Seite von Sicherheit und Kontrolle zu stehen. Geschwindigkeit, Reichweite oder virale Attraktivität rechtfertigen keine vorhersehbaren Schäden.

### 10. Verantwortung endet nicht beim Code

Auch Betrieb, Rollout, Standardkonfiguration, Beispiele, Demos, Dokumentation und Support sind Teil der Verantwortung. Unsichere Defaults oder missverständliche Einstiegspfade können genauso schädlich sein wie schlechter Kerncode.

## Was das für Ananta konkret bedeutet

Ananta orientiert sich deshalb bewusst an folgenden Leitlinien:

- Goal -> Plan -> Task -> Execution -> Verification -> Artifact statt unkontrollierter Ein-Schritt-Autorität
- Governance, Capability-Bindung und Policy-Entscheidungen als Kern statt als spätere Option
- explizite Exposition und kontrollierte Freigaben statt implizit offener Wirkung
- sichtbare Prüfungen, erklärbare Blockierungen und nachvollziehbare Ergebnisse
- harte Release- und Build-Disziplin für reproduzierbaren Betrieb
- Web, CLI und API als kontrollierte Kernzugänge vor wahlloser Kanalexpansion

## Nicht unser Verständnis von Fortschritt

Wir halten es nicht für Fortschritt,

- Agenten möglichst schnell möglichst viele Dinge tun zu lassen,
- Kontrolle als Reibung abzuwerten,
- Prüfschritte als hinderlich zu behandeln,
- riskante Defaults später vielleicht zu härten,
- reale Schäden hinter Produktbegeisterung zu verstecken.

## Unser Anspruch

Ein gutes Agentensystem ist nicht dasjenige, das am spektakulärsten wirkt, sondern dasjenige, das unter realen Bedingungen verantwortbar bleibt.

Verantwortliche Agentenentwicklung heißt für uns:

- begrenzte Macht
- sichtbare Verantwortung
- nachvollziehbare Entscheidungen
- kontrollierte Ausführung
- ehrliche Kommunikation

Alles andere ist für wirkungsmächtige Systeme zu wenig.

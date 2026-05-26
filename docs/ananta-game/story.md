# Die Geschichte von Ananta und den Spielen darin

Ananta begann nicht als klassisches Spiel. Es begann als ein Werkzeug gegen ein ziemlich modernes Problem: KI-Agenten koennen inzwischen Code schreiben, Dateien aendern, Tools ausfuehren und ganze Projekte umbauen. Aber je mehr Macht sie bekommen, desto wichtiger wird die Frage: Wer kontrolliert eigentlich die Agenten?

Der absurde und gleichzeitig passende Kern von Ananta ist deshalb: Ein Agentensystem wird mit Hilfe von Agenten gebaut, von anderen Agenten gegengeprueft, damit Agenten spaeter nicht unkontrolliert eskalieren. Der Mensch steht nicht mehr an jeder einzelnen Codezeile, sondern an der Systemgrenze. Er gibt Richtung, Regeln, Misstrauen, Akzeptanzkriterien und Verantwortung vor.

Genau daraus entsteht die Spielidee.

## Die Welt

In der Welt von Ananta ist Software kein flacher Ordner voller Dateien. Eine Codebasis ist eine Karte: Module werden zu Territorien, Abhaengigkeiten zu Wegen, Risiken zu dunklen Zonen, Tests zu Festungen, Artefakte zu Beweisen und Secrets zu gesperrten Kammern.

Agenten sind keine allwissenden Zauberer. Sie sind Einheiten auf dieser Karte. Manche koennen Code analysieren, manche refactoren, manche testen, manche dokumentieren. Aber keine Einheit darf automatisch alles sehen oder alles tun. Wer zu viel Kontext bekommt, kann Schaden anrichten. Wer zu wenig Kontext bekommt, halluziniert. Das Spiel entsteht genau in dieser Spannung.

## Der Spieler

Der Spieler ist nicht einfach ein Coder. Er ist der Architekt eines kontrollierten Agentensystems.

Er entscheidet:

- welche Agenten arbeiten duerfen,
- welche Codebereiche sichtbar sind,
- welche Aktionen Evidence brauchen,
- wann ein Ergebnis akzeptiert wird,
- wann ein Worker gestoppt oder zurueckgesetzt wird,
- wann Kontext freigegeben oder verborgen bleibt.

Das Ziel ist nicht, moeglichst schnell KI auf alles loszulassen. Das Ziel ist, Fortschritt zu erzeugen, ohne die Kontrolle zu verlieren.

## AegisHub: die Hauptbasis

Im Zentrum steht der **AegisHub**. Er ist die Hauptbasis, das Kontrollzentrum und die letzte Autoritaet. Agenten koennen Vorschlaege machen, Tasks ausfuehren und Artefakte liefern, aber der Hub bleibt Owner von Ziel, Plan, Policy, Approval und Audit.

Im Spiel ist der AegisHub der Ort, an dem Entscheidungen zusammenlaufen. Hier wird geplant, delegiert, geprueft und notfalls abgebrochen.

## AegisFlow: der Weg von Ziel zu Beweis

**AegisFlow** ist der kontrollierte Ablauf: Goal -> Plan -> Task -> Execution -> Verification -> Artifact.

Im Spiel bedeutet das: Ein Ziel ist noch kein Fortschritt. Ein Task ist noch kein Sieg. Eine Agentenantwort ist noch kein Beweis. Erst wenn ein Artefakt erzeugt, geprueft und nachvollziehbar wurde, zaehlt es wirklich.

## CodeCompass: die Karte des Codes

**CodeCompass** macht aus einem Repository eine navigierbare Welt. Es zeigt Pfade, Module, Beziehungen, Abhaengigkeiten und relevante Kontexte.

Im Spiel ist CodeCompass das Scout-System. Es hilft dem Spieler zu erkennen, welche Gebiete zusammenhaengen, wo Risiken liegen und welcher Agent welchen Ausschnitt wirklich braucht.

## ContextAegis: Nebel des Krieges fuer KI-Agenten

**ContextAegis** ist der Schutz von Kontextzugriff. Nicht jeder Agent darf jede Datei sehen. Nicht jeder Worker darf Secrets kennen. Nicht jeder Cloud-Agent darf internes Wissen bekommen.

Im Spiel wird daraus Fog of War. Manche Gebiete sind sichtbar, manche verborgen, manche redacted, manche komplett gesperrt. Kontext ist keine Selbstverstaendlichkeit, sondern eine strategische Ressource.

## CodeAegis und DevAegis: Schutzschilde fuer echte Entwicklung

**CodeAegis** schuetzt Code-Territorien gegen riskante Aktionen. Es verhindert, dass Agenten unkontrolliert an kritischen Bereichen schreiben.

**DevAegis** schuetzt den Entwicklungsfluss: Tests, CI, Branches, Reviews und Deployments werden zu Verteidigungsanlagen. Ein kaputter Build ist kein kosmetischer Fehler, sondern ein Angriff auf die Stabilitaet der Karte.

## AgentAegis: Agenten mit Grenzen

**AgentAegis** ist die Schutzschicht um die Agenten selbst. Jeder Agent hat Rollen, Faehigkeiten und Grenzen. Default-Deny ist kein Bonus, sondern Grundregel.

Im Spiel kann ein Agent stark sein, aber nie grenzenlos. Eine Einheit ohne Begrenzung ist kein Held, sondern ein Risiko.

## ArtifactGuard: kein Sieg ohne Evidence

**ArtifactGuard** bewacht den Unterschied zwischen Behauptung und Ergebnis.

Eine LLM-Antwort kann nett klingen. Ein echter Fortschritt braucht Artefakte: geaenderte Dateien, Tests, Logs, Diffs, Reviews oder reproduzierbare Outputs. Im Spiel gewinnt man nicht durch Gerede, sondern durch belegte Wirkung.

## TrustWeave: Vertrauen als Netz, nicht als Bauchgefuehl

**TrustWeave** verbindet Agenten, Codebereiche, Artefakte, Policies und Spielerentscheidungen. Vertrauen entsteht nicht automatisch. Es waechst durch erfolgreiche, belegte Aktionen und sinkt durch fehlgeschlagene oder riskante Schritte.

Im Spiel wird Vertrauen zu einem sichtbaren Graphen. Gute Evidence staerkt Wege. Verletzte Policies zerreissen Verbindungen.

## NagaCore: die Schlange im System

**NagaCore** ist der mythische Kern von Ananta. Die Schlange steht fuer Bewegung, Wiederkehr, Endlosigkeit und Wachsamkeit. Sie ist nicht die Sicherheitsautoritaet. Sie ist Guide, Symbol, Tutorial-Figur und Erinnerung daran, dass das System lebendig wirkt, aber begrenzt bleiben muss.

Die Naga kann den Spieler begleiten, warnen, erklaeren und durch die Karte fuehren. Aber sie entscheidet nicht anstelle von Policy, Hub oder Evidence.

## Der Meta-Witz

Ananta ist auch deshalb besonders, weil es seine eigene Absurditaet nicht versteckt.

Ein Mensch hat eine Reihe wilder, halbironischer, aber strukturell sehr brauchbarer Ideen: KI-Agenten, die KI-Agenten kontrollieren; ein Strategiespiel ueber sichere Softwareentwicklung; eine Schlange als Tutorial-Figur; Artefakte als Siegbedingungen; Kontextzugriff als Fog of War.

Dann bauen KI-Systeme grosse Teile davon mit: ChatGPT plant, formuliert, strukturiert und erzeugt Dateien. Claude prueft kritisch mit. Gemini stolpert manchmal herum, ist gelegentlich lustig und ab und zu doch hilfreich.

Das ist komisch. Aber es ist nicht sinnlos. Genau diese Entstehung ist die ehrlichste Demo fuer das Projekt:

> Ananta entsteht in der Praxis aus kontrollierter KI-Nutzung. Nicht blind, nicht magisch, nicht ohne Aufsicht. Sondern mit Regeln, Reviews, Akzeptanzkriterien, Misstrauen und Artefakten.

## Die Spiele in Ananta

Die Spiele in Ananta sollen keine abgetrennten Spielereien sein. Sie sind erklaerbare Simulationen der Plattform.

Ein Strategiespiel zeigt, wie Agenten, Code, Policies und Evidence zusammenhaengen.

Ein TUI-Spiel kann zeigen, wie eine KI-Snake durch Artefakte, Logs und Codebereiche navigiert.

Ein Lernspiel kann erklaeren, warum Least Privilege, Kryptographie, Kontextgrenzen und Audit wichtig sind.

Ein spaeteres Capoeira- oder Bewegungs-/3D-Terminalspiel kann zeigen, dass Ananta nicht nur Code automatisiert, sondern auch kreative Projekte unter kontrollierten Agentenbedingungen entwickeln kann.

Die Spiele sind damit Beispielprojekte, Lernumgebung und Architekturmetapher zugleich.

## Das eigentliche Thema

Unter der Mythologie, dem Humor und der Snake steckt ein ernstes Ziel:

KI-Agenten sollen nicht einfach Macht bekommen. Sie sollen in einem System arbeiten, das Grenzen kennt.

Ananta sagt deshalb:

- Kein Agent ohne Rolle.
- Kein Kontext ohne Freigabe.
- Kein Task ohne Trace.
- Kein Fortschritt ohne Artefakt.
- Kein Vertrauen ohne Evidence.
- Keine Automation ohne Kontrolle.

Das ist die Geschichte von Ananta: Ein halb absurdes, halb sehr ernstes Projekt, das mit KI gebaut wird, um KI benutzbar, pruefbar und begrenzbar zu machen.

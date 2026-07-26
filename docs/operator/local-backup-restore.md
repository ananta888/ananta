# Lokales Backup und isolierter Restore-Drill

Dieses Runbook beschreibt die Host-CLIs für den lokalen
`compose.dev.ollama.yml`-Stack unter WSL2:

- `scripts/ananta-backup.py` erzeugt ein verschlüsseltes Backup und veröffentlicht
  identische Pakete in WSL sowie auf dem von Windows gemeldeten Desktop.
- `scripts/ananta-restore.py` entschlüsselt ein Paket ausschließlich in ein
  neues oder leeres Drill-Verzeichnis und prüft dessen Wiederherstellbarkeit.

Die CLIs laufen auf dem WSL-Host. Sie sind kein Worker-Task und verändern die
Hub-Worker-Orchestrierung nicht.

## Schutzmodell und Grenzen

Ein Paketverzeichnis enthält ausschließlich:

- `<name>.tar.zst.gpg`: den mit OpenPGP verschlüsselten Payload;
- `checksum.json`: Name, Größe und SHA-256 des Ciphertexts, aber keine
  entschlüsselten Nutzdaten oder Secrets.

Damit gelangen keine unverschlüsselten Ananta-Nutzdaten auf Windows. Das
Backup ist absichtlich nicht OpenPGP-signiert: OpenPGP schützt hier
Vertraulichkeit und Integrität, der äußere SHA-256 erkennt außerdem
Kopierfehler. Beides belegt jedoch nicht die Herkunft des Pakets. Jeder, der
den öffentlichen Recovery-Key kennt, kann ein neues, formal gültiges Paket für
diesen Empfänger erzeugen. Der Restore-Bericht hält diese Grenze als
`origin_authenticity: not_verified_unsigned_openpgp_v1` fest.

Der Backup-Lauf pausiert Hub, Alpha und Beta für den konsistenten Snapshot kurz.
Danach lässt er nur einen Stand ohne Hub-Tasks in `assigned`, `delegated`,
`in_progress`, `proposing`, `running` oder `uncertain` zu. Dabei ist
`in_progress` der kanonische Task-Zustand, während der Workflow-Hub-Vertrag
diesen Ausführungszustand als `running` abbildet. `uncertain` wird ebenfalls
gesperrt, weil dabei nicht sicher ausgeschlossen werden kann, dass eine
delegierte Ausführung noch läuft. Dieses Gate ist kein vollständiges
anwendungsspezifisches Hub-Drain-Protokoll. Vor dem Lauf müssen daher alle
Ausführungen regulär beendet und neue Eingaben gestoppt werden.

## Voraussetzungen

Vor einem Backup müssen verfügbar sein:

- ein laufender lokaler Compose-Stack mit Hub, Alpha, Beta und PostgreSQL;
- Python 3 mit den Projektabhängigkeiten, insbesondere `cryptography` für die
  private/öffentliche Ed25519-Schlüsselprüfung beim Restore;
- Docker Compose, `gpg`, `zstd`, `powershell.exe` und `wslpath` in WSL;
- ausreichend freier Speicher im WSL-Ziel und auf dem Windows-Desktop;
- ein eigener OpenPGP-Recovery-Public-Key im verwendeten GPG-Keyring;
- eine Empfängerdatei mit genau einem vollständigen 40- oder
  64-stelligen hexadezimalen Fingerprint.

Der private Recovery-Key gehört nicht auf den normalen Ananta-Rechner. Er muss
passwortgeschützt, getrennt und offline aufbewahrt werden. Dieses Runbook
erzeugt bewusst keinen ungeschützten permanenten Test-Key. Ohne den eigenen
Recovery-Public-Key kann zwar ein technischer Test mit einem ephemeren Key
erfolgen, aber noch keine dauerhafte reale Sicherung angelegt werden.

Den öffentlichen Recovery-Key in den für das Backup verwendeten GPG-Keyring
importieren und den Fingerprint kontrollieren:

```bash
gpg --import /pfad/zum/ananta-recovery-public.asc
gpg --with-colons --fingerprint <VOLLSTAENDIGER_FINGERPRINT>
```

Die Empfängerdatei als normale, nicht verlinkte Datei anlegen, nur für den
eigenen Benutzer beschreibbar machen und genau den vollständigen Fingerprint
eintragen:

```bash
install -d -m 700 /home/<linux-benutzer>/.config/ananta
install -m 600 /dev/null \
  /home/<linux-benutzer>/.config/ananta/backup-recipient.fpr
```

Kommentare sind erlaubt, aber es muss genau eine nicht auskommentierte
Fingerprint-Zeile vorhanden sein. Die CLI lehnt verkürzte Key-IDs, Symlinks,
Hardlinks und gruppen- oder weltbeschreibbare Empfängerdateien ab.

## Ziele sicher festlegen

Das WSL-Ziel muss ein privates Verzeichnis im Linux-Dateisystem und
vollständig außerhalb von `/mnt` sein. Ein Beispiel ist:

```bash
install -d -m 700 /home/<linux-benutzer>/ananta-backups
install -d -m 700 /home/<linux-benutzer>/ananta-restore-drills
```

Ein Ziel unter `/mnt/c`, `/mnt/wsl` oder einem anderen `/mnt`-Unterverzeichnis
ist für WSL-Backup und Restore unzulässig, weil dort entschlüsselte temporäre
Daten auf einem interoperablen Dateisystem landen könnten.

Das Windows-Ziel ist nicht frei wählbar. Es muss exakt dem aktuellen
Windows-Known-Folder `DesktopDirectory` entsprechen. Das ist bei umgeleiteten
Desktops häufig ein OneDrive-Pfad. Den Wert in WSL anzeigen:

```bash
powershell.exe -NoProfile -NonInteractive -Command \
  '[Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)'
```

Den ausgegebenen Windows-Pfad mit `wslpath -u '<Windows-Pfad>'` in einen
absoluten WSL-Pfad umwandeln und genau diesen Wert an `--windows-target`
übergeben. Die CLI fragt den Known Folder selbst erneut bei Windows ab und
bricht bei einem abweichenden Pfad ab.

## Routinemäßiges State-Backup

Die folgenden Befehle werden vom Repository-Root ausgeführt. Zuerst den
vollständigen Preflight ohne Paket-/Compose-Schreibvorgang und ohne Pause
ausführen:

```bash
python3 scripts/ananta-backup.py \
  --wsl-target /home/<linux-benutzer>/ananta-backups \
  --windows-target '/mnt/c/Users/<windows-benutzer>/<known-folder-desktop>' \
  --recipient-file \
    /home/<linux-benutzer>/.config/ananta/backup-recipient.fpr \
  --dry-run
```

Danach denselben Befehl ohne `--dry-run` starten:

```bash
python3 scripts/ananta-backup.py \
  --wsl-target /home/<linux-benutzer>/ananta-backups \
  --windows-target '/mnt/c/Users/<windows-benutzer>/<known-folder-desktop>' \
  --recipient-file \
    /home/<linux-benutzer>/.config/ananta/backup-recipient.fpr
```

Ohne `--name` verwendet die CLI einen UTC-Zeitstempel. Vorhandene
Paketverzeichnisse werden weder ersetzt noch zusammengeführt. Standardmäßig
werden `docker/compose-next/compose.dev.ollama.yml` und `.env` verwendet; ein
bewusst abweichender Stack muss mit `--compose-file` und `--env-file`
vollständig angegeben werden.

Ein State-Backup enthält verschlüsselt:

- `.env`, gerenderte Compose-Konfiguration, Modellprofile und Modellrouting;
- Git-Commit und Dirty-State zur Reproduzierbarkeit;
- einen PostgreSQL-Custom-Dump samt `pg_restore`-Katalog;
- die Daten-Volumes von Hub, Alpha und Beta;
- die Workflow-Credentials und die Goal-/Projekt-Workspaces.

Ein Backup nur des Repository-Verzeichnisses `data/` ist deshalb unvollständig
und nicht unterstützt.

## Ollama-Modelle getrennt sichern

Das routinemäßige State-Backup lässt Modellblobs aus, damit es klein und schnell
bleibt. Modelle werden seltener als zusätzliches, separates Paket gesichert:

```bash
python3 scripts/ananta-backup.py \
  --wsl-target /home/<linux-benutzer>/ananta-backups \
  --windows-target '/mnt/c/Users/<windows-benutzer>/<known-folder-desktop>' \
  --recipient-file \
    /home/<linux-benutzer>/.config/ananta/backup-recipient.fpr \
  --include-ollama-models
```

Dieses zusätzliche Paket ist selbstständig und enthält den State plus
ausschließlich den Unterbaum `.ollama/models`. Die Ollama-Hostidentität
`.ollama/id_ed25519` und andere Dateien unter `.ollama` werden niemals
aufgenommen. Ein Modellpaket benötigt ungefähr die Größe der lokal vorhandenen
Blobs zusätzlich als freien Arbeits- und Zielspeicher.

## Kopien offline aufbewahren

Die WSL- und Desktop-Kopie schützen gegen einen einzelnen Pfadfehler, sind aber
keine zwei Offline-Backups. Ein OneDrive-Desktop ist insbesondere weiterhin
online und synchronisiert.

Nach erfolgreicher Prüfsumme müssen die verschlüsselten Paketverzeichnisse auf
mindestens zwei physisch getrennte Offline-Datenträger kopiert, dort erneut
gegen `checksum.json` geprüft und anschließend vom Rechner getrennt werden.
Der private Recovery-Key und seine Passphrase werden getrennt von den
Backup-Datenträgern verwahrt. Regelmäßige Restore-Drills bleiben erforderlich;
eine vorhandene Datei allein belegt noch keine Wiederherstellbarkeit.

## Isolierter Restore-Drill

Der Restore ist ausschließlich ein Verifikations-Drill. Er importiert nichts
in laufende Ananta-Dienste, Docker-Volumes oder Bind-Mounts. Das Ziel muss neu
oder leer sein, außerhalb `/mnt`, außerhalb des Repositorys und außerhalb
aller im verschlüsselten Compose-Snapshot aufgezeichneten Live-Bind-Roots
liegen.

Der Restore kopiert den Ciphertext zunächst einmal in ein privates temporäres
Verzeichnis auf dem WSL-Dateisystem. Prüfsummen-Vorlauf und vollständige
Entschlüsselung lesen ausschließlich diese private, vor und zwischen den
Schritten erneut geprüfte Arbeitskopie; danach wird sie entfernt. Dadurch kann
eine parallel laufende OneDrive-Synchronisation
nicht unbemerkt zwei unterschiedliche Archivstände in einem Drill vermischen.
Für diese Kopie muss in WSL vorübergehend noch einmal die Größe des
verschlüsselten Pakets frei sein.

Zuerst kann ohne Private-Key und ohne Entschlüsselung die äußere Prüfsumme
kontrolliert werden:

```bash
python3 scripts/ananta-restore.py \
  --package \
    /home/<linux-benutzer>/ananta-backups/<ananta-backup-verzeichnis> \
  --target \
    /home/<linux-benutzer>/ananta-restore-drills/<eindeutiger-drill-name> \
  --dry-run
```

Für den vollständigen Drill muss GPG in einer dedizierten
Recovery-Umgebung Zugriff auf den geschützten privaten Schlüssel haben. Zum
Beispiel kann der Prozess einen eigens dafür vorgesehenen `GNUPGHOME`
verwenden. Danach:

```bash
python3 scripts/ananta-restore.py \
  --package \
    /home/<linux-benutzer>/ananta-backups/<ananta-backup-verzeichnis> \
  --target \
    /home/<linux-benutzer>/ananta-restore-drills/<eindeutiger-drill-name>
```

Der vollständige Drill prüft:

- Ciphertext-Größe und SHA-256 sowie die internen Payload-Prüfsummen;
- sichere Archivpfade und begrenzte Archivgrößen;
- PostgreSQL-Dump und echte `pg_restore`-Ausführung;
- Hub-/Worker-Volume-Archive und die SQLite-Integrität von Alpha und Beta;
- Struktur, Kryptografie- und Registrierungsbindung der Workflow-Credentials;
- bei enthaltenen Modellen die Ollama-Manifeste und referenzierten Blobs.

`pg_restore` läuft in einem wegwerfbaren PostgreSQL-Container mit dem im
Backup festgehaltenen Image, ohne Netzwerk, veröffentlichte Ports oder
persistente Volumes. Daten liegen nur in begrenzten `tmpfs`-Mounts. Der
Container wird nach dem Drill gestoppt; laufende Ananta-Container werden nicht
verwendet. Ein erfolgreicher Lauf schreibt `RESTORE_VERIFIED.json` in das
Drill-Ziel. Das entschlüsselte Drill-Verzeichnis enthält Secrets und muss nach
Auswertung wie sensibles Klartextmaterial behandelt und sicher entfernt
werden.

## Acceptance-Nachweis vom 2026-07-26

Der vollständige lokale Acceptance-Lauf erzeugte ein Paket mit:

- Ciphertext-Größe: `732530` Bytes;
- SHA-256:
  `6f01c7e9129db8ef90a3a984c0b147e1db69babaf4a22ede218e846c036b8c14`;
- identischer WSL- und Windows-Known-Folder-Desktop-Kopie;
- Restore aus der Windows-/OneDrive-Kopie über genau einen privaten,
  nach jedem Archivdurchlauf erneut gehashten WSL-Ciphertext-Snapshot;
- erfolgreichem `pg_restore`, erfolgreichen SQLite-Integritätsprüfungen und
  erfolgreicher Credential-Prüfung.

Die Testpakete, entschlüsselten Drill-Artefakte und der dafür ausschließlich
ephemer verwendete Schlüssel wurden danach entfernt. Dieser Acceptance-Lauf
belegt den technischen Ablauf, ist aber keine dauerhafte Sicherung des
Benutzers. Dafür ist zuerst dessen eigener Recovery-Public-Key erforderlich.

## Bekannte P2-Grenzen

- Die allgemeinen Docker-, GPG-, zstd- und Kopierprozesse besitzen noch keine
  durchgängigen harten Laufzeit-Timeouts. Der PostgreSQL-Readiness-Check ist
  begrenzt, ersetzt aber keine allgemeine Prozessfrist.
- Die Archivprüfung begrenzt Einträge und deklarierte Dateigrößen, reserviert
  aber keine harte Dateisystem-Quota für WSL-Ziel, Windows-Ziel oder
  Restore-Verzeichnis. Freier Speicher muss vor dem Lauf betrieblich geprüft
  und überwacht werden.
- Das Task-Status-Gate ist, wie oben beschrieben, kein vollständiges
  Hub-Drain-Protokoll.
- Ein `SIGKILL`, WSL-Absturz oder Stromausfall kann private
  Klartext-Arbeitsverzeichnisse mit `.work-` beziehungsweise `.restore-` im
  gewählten WSL-Ziel oder Restore-Elternverzeichnis zurücklassen. Vor einer
  manuellen Bereinigung muss ausgeschlossen sein, dass noch ein Backup- oder
  Restore-Prozess läuft; die Verzeichnisse dürfen nicht per breitem Glob
  gelöscht werden.
- Reguläre Quelldateien werden nach einer `lstat`-Inventur noch pfadbasiert
  geöffnet. Ein absichtlicher, zeitlich exakt platzierter Dateitausch durch
  einen Prozess mit derselben Linux-Identität ist daher noch nicht vollständig
  über einen `O_NOFOLLOW`-/Dateideskriptor-Kontrakt ausgeschlossen.
- Beim finalen Restore-Publish kann ein konkurrierend neu erzeugtes, leeres
  Zielverzeichnis ersetzt werden. Nichtleere Ziele, Dateien und Symlinks werden
  weiterhin abgelehnt; ein striktes atomisches No-Clobber-Publish bleibt als
  Härtung offen.
- `ComposeAdapter` bündelt weiterhin Discovery, Pause/Gate, Datenbank- und
  Volume-Capture. Eine Aufteilung in kleinere Ports bleibt als
  SRP-/DIP-Härtung offen.
- Unsigned OpenPGP authentisiert die Herkunft des Pakets nicht.

## Nicht enthalten: Bitcoin-Core-Wallet

Bitcoin Core und dessen Wallet sind noch kein Bestandteil dieses
Compose-Stacks. Wallet-Dateien, Seed-/Deskriptor-Backups und private
Bitcoin-Schlüssel werden von diesen CLIs nicht gesichert oder geprüft. Vor dem
Empfang realer Bitcoin ist dafür ein eigener, getesteter und offline
aufbewahrter Bitcoin-Core-Wallet-Backup-Prozess erforderlich.

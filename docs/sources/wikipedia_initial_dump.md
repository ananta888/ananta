# Wikipedia Initial Dump Strategy

## Ziel

Ein kontrollierter Startimport statt Vollimport:

- **Default Sprache:** `de`
- **Default Dump-Art:** `pages-articles-multistream` (XML + Index)
- **MVP-Start:** kleiner Subset-/Fixture-Import für Pipeline-Validierung

## Optionen

| Option | Vorteil | Nachteil |
|---|---|---|
| pages-articles (multistream) | Volltextnah, offizieller Dump, gute Nachvollziehbarkeit | groß, aufwendiger Parse |
| abstracts | klein, schnell | wenig Tiefe, weniger nutzbar für präzise Antworten |
| OpenZIM/Kiwix | kompaktes Paketformat | Parserkomplexität/Featuregrenzen |
| Wikimedia Enterprise HTML | strukturierter Inhalt | i. d. R. nicht frei wie öffentliche Dumps |

## Speicher/Laufzeit/Lizenz

- Speicherbedarf hängt stark von Sprache und Snapshot ab; vor produktivem Import muss freie Disk-Kapazität geprüft werden.
- Download und Parse laufen streaming/chunked.
- Attribution und Lizenz (CC BY-SA) sind Pflichtbestandteil pro Chunk.

## Update-Frequenz

- Initial: manuell gesteuerter Refresh
- Danach: geplanter Refresh gemäß `refresh_interval` aus dem SourceDescriptor

## Referenzen

- https://dumps.wikimedia.org/
- https://meta.wikimedia.org/wiki/Data_dumps

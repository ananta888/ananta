# Ananta Eclipse Runtime Plugin

Dieses Modul enthält das Eclipse-Plugin inkl. **Ananta Snake Overlay** für den Eclipse-IDE-Kontext.

## Modulpfad

- `client_surfaces/eclipse_runtime/ananta_eclipse_plugin`

## Build

```bash
cd client_surfaces/eclipse_runtime/ananta_eclipse_plugin
gradle clean test build
```

Optionales Bundle-Artefakt:

```bash
gradle eclipsePluginBundle
```

## Start in lokaler Eclipse-Instanz

1. Bundle bauen.
2. Plugin in eine Eclipse Runtime/Target-Instanz einbinden (PDE Run Configuration oder Update-Site/Dropins-Flow).
3. In Eclipse den Command **Ananta Snake Toggle** nutzen.
4. View **Ananta Snake** öffnen, falls nicht automatisch sichtbar.

## Enthaltene Snake-Basisfunktionen

- Lazy aktivierbares Plugin mit Command/Toolbar/Menu-Eintrag.
- Zentraler `AnantaSnakePluginService` für Start/Stop/Restart und Snake-Status.
- SWT-basierte Overlay-Canvas-Komponente mit einfacher Segmentdarstellung.
- Mauskoordinaten-Normalisierung für Workbench-/Overlay-Koordinaten.

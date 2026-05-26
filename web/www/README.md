# www.ananta.de

Dieses Verzeichnis enthält die statischen Dateien für die öffentliche Ananta-Webseite.

Deployment:

- Quelle: `web/www/` im Repo `ananta888/ananta`
- Workflow: `.github/workflows/pages-www.yml`
- Ziel-Repo: `ananta888/ananta888.github.io` (Branch `main`, Root)
- Gewünschte Domain: `www.ananta.de` (über `CNAME` in den veröffentlichten Dateien)

Einmalige GitHub-Konfiguration:

1. Im Repo `ananta888/ananta` ein Secret `ANANTA888_GH_IO_TOKEN` anlegen.
2. Token-Rechte: `Contents: Read and write` auf `ananta888/ananta888.github.io`.
3. Im Repo `ananta888/ananta888.github.io` unter `Settings` → `Pages` als Source `Deploy from a branch` (Branch `main`, `/root`) setzen.
4. `Custom domain` = `www.ananta.de` und danach `Enforce HTTPS` aktivieren.

DNS beim Domainanbieter:

```text
www   CNAME   ananta888.github.io
```

Bestehende DynDNS-Subdomains bleiben davon unabhängig.

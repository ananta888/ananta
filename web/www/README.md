# www.ananta.de

Dieses Verzeichnis enthält die statischen Dateien für die öffentliche Ananta-Webseite.

Deployment:

- Quelle: `web/www/`
- Workflow: `.github/workflows/pages-www.yml`
- Ziel: GitHub Pages
- Gewünschte Domain: `www.ananta.de`

Nach dem Merge muss in GitHub einmalig eingestellt werden:

1. Repository `Settings` → `Pages`
2. `Build and deployment` → `Source` = `GitHub Actions`
3. `Custom domain` = `www.ananta.de`
4. Nach erfolgreicher DNS-Prüfung `Enforce HTTPS` aktivieren

DNS beim Domainanbieter:

```text
www   CNAME   ananta888.github.io
```

Bestehende DynDNS-Subdomains bleiben davon unabhängig.

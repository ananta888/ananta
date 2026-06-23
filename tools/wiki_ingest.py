#!/usr/bin/env python3
"""CLI: Wikipedia-Dump indizieren und in Ananta-Wissensbasis einpflegen.

Verwendung:
  # Lokale XML.BZ2-Datei indizieren:
  python -m tools.wiki_ingest --dump /pfad/zu/dewiki-latest.xml.bz2

  # Multistream-Dump mit Index:
  python -m tools.wiki_ingest \\
    --dump /pfad/zu/dewiki-latest-pages-articles-multistream.xml.bz2 \\
    --index /pfad/zu/dewiki-latest-pages-articles-multistream-index.txt.bz2

  # Per URL (Download + Indizierung):
  python -m tools.wiki_ingest \\
    --url https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream.xml.bz2 \\
    --index-url https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream-index.txt.bz2

  # Vorschau (kein echtes Indexieren, max 200 Artikel):
  python -m tools.wiki_ingest --dump /pfad/... --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_log = logging.getLogger("wiki_ingest")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wikipedia-Dump in Ananta-Wissensbasis importieren")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--dump", metavar="PATH", help="Lokale XML.BZ2-Datei")
    src.add_argument("--url", metavar="URL", help="Corpus-URL (wird heruntergeladen)")
    p.add_argument("--index", metavar="PATH", help="Lokaler Multistream-Index (.txt.bz2)")
    p.add_argument("--index-url", metavar="URL", help="Multistream-Index-URL")
    p.add_argument("--source-id", default="", help="Source-ID (default: aus Dateiname)")
    p.add_argument("--language", default="de", help="Sprache (default: de)")
    p.add_argument("--profile", default="default", help="Knowledge-Index-Profil")
    p.add_argument("--codecompass-prerender", action="store_true", default=True,
                   help="CodeCompass-Vorrendering aktivieren (default: an)")
    p.add_argument("--no-codecompass-prerender", dest="codecompass_prerender", action="store_false")
    p.add_argument("--strict", action="store_true", help="Strikter Modus: Fehler abbrechen")
    p.add_argument("--dry-run", action="store_true", help="Nur parsen, nicht indexieren (max 200 Artikel)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _run_local(args: argparse.Namespace) -> None:
    from agent.services.ingestion_service import get_ingestion_service
    from agent.services.knowledge_index_job_service import get_knowledge_index_job_service

    dump_path = Path(args.dump).resolve()
    if not dump_path.exists():
        _log.error("Dump-Datei nicht gefunden: %s", dump_path)
        sys.exit(1)

    source_id = args.source_id or dump_path.stem.replace(".xml", "").replace(".bz2", "")
    index_path = Path(args.index).resolve() if args.index else None

    _log.info("Starte Ingest: %s (source_id=%s, language=%s)", dump_path.name, source_id, args.language)
    t0 = time.monotonic()

    svc = get_ingestion_service()
    report = svc.import_wiki_xml(
        corpus_path=dump_path,
        index_path=index_path,
        source_id=source_id,
        default_language=args.language,
        strict=args.strict,
        dry_run_limit=200 if args.dry_run else None,
    )

    stats = report.get("stats") or {}
    _log.info(
        "Parse abgeschlossen: %d Records, %d Issues (%.1fs)",
        stats.get("normalized_records") or stats.get("records_total") or 0,
        len(report.get("issues") or []),
        time.monotonic() - t0,
    )

    if args.dry_run:
        _log.info("Dry-run: Indexierung uebersprungen.")
        return

    _log.info("Starte Indexierung...")
    job_svc = get_knowledge_index_job_service()
    job = job_svc.submit_source_records_job(
        source_scope="wiki",
        source_id=source_id,
        records=list(report.get("records") or []),
        codecompass_prerender=args.codecompass_prerender,
        profile_name=args.profile,
    )
    job_id = str(job.get("job_id") or "")
    _log.info("Job gestartet: %s", job_id)
    _poll_job(job_svc, job_id)


def _run_url(args: argparse.Namespace) -> None:
    from agent.services.ingestion_service import get_ingestion_service
    from agent.services.knowledge_index_job_service import get_knowledge_index_job_service

    source_id = args.source_id or args.url.rstrip("/").split("/")[-1].split(".")[0]
    _log.info("Download + Ingest von: %s", args.url)
    t0 = time.monotonic()

    svc = get_ingestion_service()
    report = svc.import_wiki_jsonl_from_url(
        corpus_url=args.url,
        index_url=args.index_url or None,
        source_id=source_id,
        default_language=args.language,
        strict=args.strict,
    )

    stats = report.get("stats") or {}
    _log.info(
        "Parse abgeschlossen: %d Records, %d Issues (%.1fs)",
        stats.get("normalized_records") or stats.get("records_total") or 0,
        len(report.get("issues") or []),
        time.monotonic() - t0,
    )

    if args.dry_run:
        _log.info("Dry-run: Indexierung uebersprungen.")
        return

    _log.info("Starte Indexierung...")
    job_svc = get_knowledge_index_job_service()
    job = job_svc.submit_source_records_job(
        source_scope="wiki",
        source_id=source_id,
        records=list(report.get("records") or []),
        codecompass_prerender=args.codecompass_prerender,
        profile_name=args.profile,
    )
    job_id = str(job.get("job_id") or "")
    _log.info("Job gestartet: %s", job_id)
    _poll_job(job_svc, job_id)


def _poll_job(job_svc, job_id: str) -> None:
    import time as _time
    while True:
        job = job_svc.get_job(job_id)
        if not job:
            _log.error("Job nicht gefunden: %s", job_id)
            sys.exit(1)
        status = str(job.get("status") or "").lower()
        phase = str(job.get("phase") or "")
        pct = job.get("progress_percent") or 0
        _log.info("Job %s: status=%s phase=%s progress=%s%%", job_id, status, phase, pct)
        if status == "completed":
            _log.info("Indexierung abgeschlossen.")
            return
        if status in ("failed", "cancelled"):
            _log.error("Job fehlgeschlagen: %s", job.get("error") or status)
            sys.exit(1)
        _time.sleep(5)


def main() -> None:
    args = _parse_args()
    _setup_logging(args.verbose)
    if args.url:
        _run_url(args)
    else:
        _run_local(args)


if __name__ == "__main__":
    main()

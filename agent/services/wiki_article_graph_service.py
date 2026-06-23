"""Wiki Article Graph Service — builds and queries a SQLite article-graph index.

The full wiki graph_nodes.jsonl (1.7 GB) + graph_edges.jsonl (7.9 GB) are too large
to load directly into the browser graph-viewer.  This service extracts only
  • wiki_article nodes  (~2.9 M rows)
  • wiki_link edges     (~65 M rows)
into a SQLite database so neighborhood queries return in milliseconds.

The database lives next to the JSONL files:
  <output_dir>/wiki_article_graph.db
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_BUILD_STATUS: dict[str, dict[str, Any]] = {}


def _db_path(output_dir: Path) -> Path:
    return output_dir / "wiki_article_graph.db"


def get_build_status(output_dir: Path) -> dict[str, Any]:
    key = str(output_dir)
    with _LOCK:
        cached = _BUILD_STATUS.get(key)
    if cached:
        return dict(cached)
    db = _db_path(output_dir)
    if not db.exists():
        return {"status": "not_built", "db_path": str(db)}
    try:
        with sqlite3.connect(str(db), timeout=5) as con:
            article_count = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            edge_count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {
            "status": "ready",
            "db_path": str(db),
            "article_count": article_count,
            "edge_count": edge_count,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def build_index(output_dir: Path, *, force: bool = False) -> None:
    """Build the SQLite article-graph index in the calling thread (run in a background thread)."""
    key = str(output_dir)
    db = _db_path(output_dir)
    nodes_path = output_dir / "graph_nodes.jsonl"
    edges_path = output_dir / "graph_edges.jsonl"

    if not nodes_path.exists() or not edges_path.exists():
        _set_status(key, {"status": "error", "error": "graph_nodes.jsonl or graph_edges.jsonl missing"})
        return
    if db.exists() and not force:
        return  # already built

    _set_status(key, {"status": "building", "phase": "nodes", "started_at": time.time()})
    try:
        if db.exists():
            db.unlink()
        con = sqlite3.connect(str(db), isolation_level=None)  # autocommit for speed
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=-131072")  # 128 MB
        con.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                slug TEXT PRIMARY KEY,
                title TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts
            USING fts5(slug, title, content='articles', tokenize='unicode61')
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                from_slug TEXT NOT NULL,
                to_slug TEXT NOT NULL
            )
        """)

        # ── Pass 1: article nodes ──────────────────────────────────────────
        article_count = 0
        BATCH = 5000
        buf: list[tuple] = []

        def _flush_articles() -> None:
            nonlocal article_count
            if not buf:
                return
            con.execute("BEGIN")
            con.executemany("INSERT OR IGNORE INTO articles(slug, title) VALUES (?,?)", buf)
            con.execute("COMMIT")
            article_count += len(buf)
            buf.clear()

        logger.info("wiki_article_graph_service: scanning nodes from %s", nodes_path.name)
        with nodes_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") != "wiki_article":
                    continue
                node_id: str = str(rec.get("node_id") or "")
                title: str = str(rec.get("title") or "")
                slug = node_id.removeprefix("article:")
                if slug and title:
                    buf.append((slug, title))
                if len(buf) >= BATCH:
                    _flush_articles()
                    if article_count % 200_000 == 0:
                        _set_status(key, {"status": "building", "phase": "nodes", "article_count": article_count})
        _flush_articles()
        logger.info("wiki_article_graph_service: %d article nodes inserted", article_count)

        # Build FTS index
        _set_status(key, {"status": "building", "phase": "fts", "article_count": article_count})
        con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        logger.info("wiki_article_graph_service: FTS index built")

        # ── Pass 2: wiki_link edges ────────────────────────────────────────
        _set_status(key, {"status": "building", "phase": "edges", "article_count": article_count})
        edge_count = 0
        buf_e: list[tuple] = []

        def _flush_edges() -> None:
            nonlocal edge_count
            if not buf_e:
                return
            con.execute("BEGIN")
            con.executemany("INSERT INTO edges(from_slug, to_slug) VALUES (?,?)", buf_e)
            con.execute("COMMIT")
            edge_count += len(buf_e)
            buf_e.clear()

        logger.info("wiki_article_graph_service: scanning edges from %s", edges_path.name)
        with edges_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("relation") != "wiki_link":
                    continue
                from_slug = str(rec.get("from") or "").removeprefix("article:")
                to_slug   = str(rec.get("to")   or "").removeprefix("article:")
                if from_slug and to_slug:
                    buf_e.append((from_slug, to_slug))
                if len(buf_e) >= BATCH:
                    _flush_edges()
                    if edge_count % 5_000_000 == 0:
                        _set_status(key, {"status": "building", "phase": "edges",
                                          "article_count": article_count, "edge_count": edge_count})
        _flush_edges()
        logger.info("wiki_article_graph_service: %d wiki_link edges inserted", edge_count)

        # Indices
        _set_status(key, {"status": "building", "phase": "index", "article_count": article_count, "edge_count": edge_count})
        con.execute("CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_slug)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_slug)")
        con.close()
        logger.info("wiki_article_graph_service: build complete — %d articles, %d edges", article_count, edge_count)
        _set_status(key, {"status": "ready", "article_count": article_count, "edge_count": edge_count, "db_path": str(db)})
    except Exception as exc:
        logger.exception("wiki_article_graph_service: build failed")
        _set_status(key, {"status": "error", "error": str(exc)})
        if db.exists():
            try:
                db.unlink()
            except Exception:
                pass


def search_articles(output_dir: Path, query: str, limit: int = 20) -> list[dict]:
    db = _db_path(output_dir)
    if not db.exists():
        return []
    q = query.strip()
    if not q:
        return []
    try:
        with sqlite3.connect(str(db), timeout=10) as con:
            # Try FTS first
            try:
                fts_query = " ".join(f'"{w}"*' for w in q.split()[:4])
                rows = con.execute(
                    "SELECT a.slug, a.title FROM articles a "
                    "JOIN articles_fts f ON a.slug = f.slug "
                    "WHERE articles_fts MATCH ? LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
                if rows:
                    return [{"slug": r[0], "title": r[1]} for r in rows]
            except sqlite3.OperationalError:
                pass
            # Fallback: LIKE
            rows = con.execute(
                "SELECT slug, title FROM articles WHERE title LIKE ? LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
            return [{"slug": r[0], "title": r[1]} for r in rows]
    except Exception as exc:
        logger.warning("wiki_article_graph_service: search error: %s", exc)
        return []


def expand_article(output_dir: Path, slug: str, *, max_neighbors: int = 40) -> dict[str, Any]:
    """Return a domain_graph_artifact.v1 subgraph centred on the given article slug."""
    db = _db_path(output_dir)
    if not db.exists():
        return {"schema": "domain_graph_artifact.v1", "nodes": [], "edges": [], "metadata": {"error": "index_not_built"}}
    try:
        with sqlite3.connect(str(db), timeout=10) as con:
            # Resolve seed
            row = con.execute("SELECT slug, title FROM articles WHERE slug = ?", (slug,)).fetchone()
            if not row:
                # Try by title
                row = con.execute("SELECT slug, title FROM articles WHERE title = ?", (slug,)).fetchone()
            if not row:
                return {"schema": "domain_graph_artifact.v1", "nodes": [], "edges": [],
                        "metadata": {"error": "article_not_found", "slug": slug}}
            seed_slug, seed_title = row

            # Outgoing neighbors (articles this one links to)
            out_rows = con.execute(
                "SELECT to_slug FROM edges WHERE from_slug = ? LIMIT ?",
                (seed_slug, max_neighbors),
            ).fetchall()
            out_slugs = [r[0] for r in out_rows]

            # Incoming neighbors (articles that link here)
            in_rows = con.execute(
                "SELECT from_slug FROM edges WHERE to_slug = ? LIMIT ?",
                (seed_slug, max_neighbors // 2),
            ).fetchall()
            in_slugs = [r[0] for r in in_rows]

            all_neighbor_slugs = list(dict.fromkeys(out_slugs + in_slugs))[:max_neighbors]

            # Fetch titles for neighbors
            if all_neighbor_slugs:
                placeholders = ",".join("?" * len(all_neighbor_slugs))
                nb_rows = con.execute(
                    f"SELECT slug, title FROM articles WHERE slug IN ({placeholders})",
                    all_neighbor_slugs,
                ).fetchall()
            else:
                nb_rows = []
            nb_map: dict[str, str] = {r[0]: r[1] for r in nb_rows}

        # Build domain_graph_artifact.v1
        nodes = [_article_node(seed_slug, seed_title, seed=True)]
        for nslug in all_neighbor_slugs:
            ntitle = nb_map.get(nslug, nslug)
            nodes.append(_article_node(nslug, ntitle, seed=False))

        slug_set = {n["node_id"] for n in nodes}
        edges = []
        for nslug in out_slugs:
            to_id = f"article:{nslug}"
            if to_id in slug_set:
                edges.append({"source_id": f"article:{seed_slug}", "target_id": to_id,
                               "relation": "wiki_link", "attributes": {"confidence": 1.0}})
        for nslug in in_slugs:
            from_id = f"article:{nslug}"
            if from_id in slug_set:
                edges.append({"source_id": from_id, "target_id": f"article:{seed_slug}",
                               "relation": "wiki_link", "attributes": {"confidence": 1.0}})

        return {
            "schema": "domain_graph_artifact.v1",
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "seed_slug": seed_slug,
                "seed_title": seed_title,
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        }
    except Exception as exc:
        logger.exception("wiki_article_graph_service: expand error")
        return {"schema": "domain_graph_artifact.v1", "nodes": [], "edges": [],
                "metadata": {"error": str(exc)}}


def _article_node(slug: str, title: str, *, seed: bool) -> dict:
    return {
        "node_id": f"article:{slug}",
        "node_type": "wiki_article",
        "attributes": {
            "name": title,
            "file": "",
            "content": "",
            "seed": seed,
        },
    }


def _set_status(key: str, status: dict) -> None:
    with _LOCK:
        _BUILD_STATUS[key] = status

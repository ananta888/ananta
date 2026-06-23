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

import bz2
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_BUILD_STATUS: dict[str, dict[str, Any]] = {}
_DOMAIN_BUILD_STATUS: dict[str, dict[str, Any]] = {}  # key = f"{output_dir}:{mode}"
_DOMAIN_LOCK = threading.Lock()
_CONTENT_LOCK = threading.Lock()
_CONTENT_BUILD_STATUS: dict[str, dict[str, Any]] = {}  # key = str(output_dir)


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


# ── Domain Mode Functions ─────────────────────────────────────────────────────

def _set_domain_status(key: str, status: dict) -> None:
    with _DOMAIN_LOCK:
        _DOMAIN_BUILD_STATUS[key] = dict(status)


def _get_domain_status_cached(key: str) -> dict | None:
    with _DOMAIN_LOCK:
        return dict(_DOMAIN_BUILD_STATUS[key]) if key in _DOMAIN_BUILD_STATUS else None


def get_domain_build_status(output_dir: Path) -> dict:
    """Returns status for all three domain modes."""
    db = _db_path(output_dir)
    result = {}
    for mode in ("hubs", "categories", "clusters"):
        key = f"{output_dir}:{mode}"
        cached = _get_domain_status_cached(key)
        if cached and cached.get("status") in ("building", "error"):
            result[mode] = cached
            continue
        # Check SQLite for table presence
        if not db.exists():
            result[mode] = {"status": "not_built"}
            continue
        try:
            with sqlite3.connect(str(db), timeout=5) as con:
                if mode == "hubs":
                    count = con.execute("SELECT COUNT(*) FROM hubs").fetchone()[0]
                    result[mode] = {"status": "ready", "count": count} if count > 0 else {"status": "not_built"}
                elif mode == "categories":
                    count = con.execute("SELECT COUNT(*) FROM top_categories").fetchone()[0]
                    result[mode] = {"status": "ready", "count": count} if count > 0 else {"status": "not_built"}
                elif mode == "clusters":
                    count = con.execute("SELECT COUNT(*) FROM top_clusters").fetchone()[0]
                    result[mode] = {"status": "ready", "count": count} if count > 0 else {"status": "not_built"}
        except sqlite3.OperationalError:
            result[mode] = {"status": "not_built"}
        except Exception as exc:
            result[mode] = {"status": "error", "error": str(exc)}
    return result


def build_domains(output_dir: Path, mode: str, corpus_path: Path | None = None) -> None:
    """Background thread entry point. Calls _build_hubs, _build_categories, or _build_clusters."""
    key = f"{output_dir}:{mode}"
    _set_domain_status(key, {"status": "building", "started_at": time.time()})
    try:
        if mode == "hubs":
            _build_hubs(output_dir, key)
        elif mode == "categories":
            if corpus_path is None:
                _set_domain_status(key, {"status": "error", "error": "corpus_path required for categories build"})
                return
            _build_categories(output_dir, key, corpus_path)
        elif mode == "clusters":
            _build_clusters(output_dir, key)
        else:
            _set_domain_status(key, {"status": "error", "error": f"unknown mode: {mode}"})
    except Exception as exc:
        logger.exception("wiki_article_graph_service: domain build failed for mode=%s", mode)
        _set_domain_status(key, {"status": "error", "error": str(exc)})


def get_domains(output_dir: Path, mode: str, limit: int = 100) -> list[dict]:
    """Return domain list for selected mode.
    hubs: [{id: slug, label: title, article_count: in_degree}]
    categories: [{id: category, label: category, article_count: n}]
    clusters: [{id: hub_slug, label: hub_title, article_count: n}]
    """
    db = _db_path(output_dir)
    if not db.exists():
        return []
    try:
        with sqlite3.connect(str(db), timeout=10) as con:
            if mode == "hubs":
                rows = con.execute(
                    "SELECT slug, title, in_degree FROM hubs ORDER BY rank ASC LIMIT ?", (limit,)
                ).fetchall()
                return [{"id": r[0], "label": r[1], "article_count": r[2]} for r in rows]
            elif mode == "categories":
                rows = con.execute(
                    "SELECT category, article_count FROM top_categories ORDER BY article_count DESC LIMIT ?", (limit,)
                ).fetchall()
                return [{"id": r[0], "label": r[0], "article_count": r[1]} for r in rows]
            elif mode == "clusters":
                rows = con.execute(
                    "SELECT hub_slug, hub_title, article_count FROM top_clusters ORDER BY article_count DESC LIMIT ?", (limit,)
                ).fetchall()
                return [{"id": r[0], "label": r[1], "article_count": r[2]} for r in rows]
    except Exception as exc:
        logger.warning("wiki_article_graph_service: get_domains error: %s", exc)
    return []


def get_domain_articles(output_dir: Path, mode: str, domain_id: str, limit: int = 50) -> list[dict]:
    """Return top articles for a domain sorted by in_degree desc.
    Returns [{slug, title, in_degree}]
    """
    db = _db_path(output_dir)
    if not db.exists():
        return []
    try:
        with sqlite3.connect(str(db), timeout=10) as con:
            if mode == "hubs":
                # Articles that directly link TO the hub article
                rows = con.execute(
                    """SELECT a.slug, a.title,
                              (SELECT COUNT(*) FROM edges e2 WHERE e2.to_slug = a.slug) AS in_degree
                       FROM edges e
                       JOIN articles a ON a.slug = e.from_slug
                       WHERE e.to_slug = ?
                       GROUP BY a.slug
                       ORDER BY in_degree DESC
                       LIMIT ?""",
                    (domain_id, limit),
                ).fetchall()
                return [{"slug": r[0], "title": r[1], "in_degree": r[2]} for r in rows]
            elif mode == "categories":
                rows = con.execute(
                    """SELECT a.slug, a.title,
                              (SELECT COUNT(*) FROM edges e WHERE e.to_slug = a.slug) AS in_degree
                       FROM article_categories ac
                       JOIN articles a ON a.slug = ac.slug
                       WHERE ac.category = ?
                       ORDER BY in_degree DESC
                       LIMIT ?""",
                    (domain_id, limit),
                ).fetchall()
                return [{"slug": r[0], "title": r[1], "in_degree": r[2]} for r in rows]
            elif mode == "clusters":
                rows = con.execute(
                    """SELECT a.slug, a.title,
                              (SELECT COUNT(*) FROM edges e WHERE e.to_slug = a.slug) AS in_degree
                       FROM article_clusters ac
                       JOIN articles a ON a.slug = ac.slug
                       WHERE ac.hub_slug = ?
                       ORDER BY in_degree DESC
                       LIMIT ?""",
                    (domain_id, limit),
                ).fetchall()
                return [{"slug": r[0], "title": r[1], "in_degree": r[2]} for r in rows]
    except Exception as exc:
        logger.warning("wiki_article_graph_service: get_domain_articles error: %s", exc)
    return []


def get_domain_graph(output_dir: Path, mode: str, domain_id: str, limit: int = 100) -> dict:
    """Return all articles of a domain plus intra-domain edges as domain_graph_artifact.v1.

    mode=hubs:      domain_id = hub slug  → hub article + its outgoing neighbors (expand-style)
    mode=categories: domain_id = category → all articles in that category
    mode=clusters:   domain_id = hub_slug → all articles in that BFS cluster
    """
    db = _db_path(output_dir)
    empty = {"schema": "domain_graph_artifact.v1", "source_kind": f"wiki_{mode}",
             "source_ref": domain_id, "nodes": [], "edges": [],
             "metadata": {"domain": domain_id, "mode": mode, "node_count": 0, "edge_count": 0},
             "warnings": []}
    if not db.exists():
        empty["warnings"] = ["index not built"]
        return empty
    try:
        # articles table only has (slug, title) — in_degree computed via subquery
        _ideg = "(SELECT COUNT(*) FROM edges e WHERE e.to_slug = a.slug)"
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30) as con:
            if mode == "hubs":
                # The "hub" IS an article — expand its neighborhood
                center = con.execute(
                    f"SELECT slug, title, {_ideg} FROM articles a WHERE a.slug = ?",
                    (domain_id,),
                ).fetchone()
                if not center:
                    return empty
                neighbors = con.execute(
                    f"""SELECT DISTINCT a.slug, a.title, {_ideg}
                        FROM edges e JOIN articles a ON a.slug = e.to_slug
                        WHERE e.from_slug = ? AND a.slug != ?
                        ORDER BY 3 DESC LIMIT ?""",
                    (domain_id, domain_id, limit - 1),
                ).fetchall()
                rows = [center] + list(neighbors)
            elif mode == "categories":
                if not _table_exists(con, "article_categories"):
                    empty["warnings"] = ["categories not built"]
                    return empty
                rows = con.execute(
                    f"""SELECT a.slug, a.title, {_ideg}
                        FROM article_categories ac JOIN articles a ON a.slug = ac.slug
                        WHERE ac.category = ? ORDER BY 3 DESC LIMIT ?""",
                    (domain_id, limit),
                ).fetchall()
            elif mode == "clusters":
                if not _table_exists(con, "article_clusters"):
                    empty["warnings"] = ["clusters not built"]
                    return empty
                rows = con.execute(
                    f"""SELECT a.slug, a.title, {_ideg}
                        FROM article_clusters ac JOIN articles a ON a.slug = ac.slug
                        WHERE ac.hub_slug = ? ORDER BY 3 DESC LIMIT ?""",
                    (domain_id, limit),
                ).fetchall()
            else:
                empty["warnings"] = [f"unknown mode: {mode}"]
                return empty

            if not rows:
                return empty

            slug_set = {r[0] for r in rows}
            placeholders = ",".join(["?"] * len(slug_set))
            slug_list = list(slug_set)
            edge_rows = con.execute(
                f"""SELECT from_slug, to_slug FROM edges
                    WHERE from_slug IN ({placeholders}) AND to_slug IN ({placeholders})
                    LIMIT 5000""",
                slug_list + slug_list,
            ).fetchall()

            nodes = [
                {"node_id": f"article:{r[0]}", "node_type": "wiki_article",
                 "attributes": {"name": r[1], "in_degree": r[2]}}
                for r in rows
            ]
            edges = [
                {"source_id": f"article:{r[0]}", "target_id": f"article:{r[1]}",
                 "relation": "wiki_link", "attributes": {}}
                for r in edge_rows if r[0] != r[1]
            ]
            return {
                "schema": "domain_graph_artifact.v1",
                "source_kind": f"wiki_{mode}",
                "source_ref": domain_id,
                "nodes": nodes,
                "edges": edges,
                "metadata": {"domain": domain_id, "mode": mode,
                             "node_count": len(nodes), "edge_count": len(edges)},
                "warnings": [],
            }
    except Exception as exc:
        logger.warning("wiki_article_graph_service: get_domain_graph error: %s", exc)
        empty["warnings"] = [str(exc)]
        return empty


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


# ── Article Content Index ─────────────────────────────────────────────────────

def get_content_status(output_dir: Path) -> dict[str, Any]:
    key = str(output_dir)
    with _CONTENT_LOCK:
        cached = _CONTENT_BUILD_STATUS.get(key)
    if cached and cached.get("status") == "building":
        return dict(cached)
    db = _db_path(output_dir)
    if not db.exists():
        return {"status": "not_built", "reason": "main index not built"}
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as con:
            if not _table_exists(con, "article_intro"):
                return {"status": "not_built"}
            count = con.execute("SELECT COUNT(*) FROM article_intro").fetchone()[0]
            return {"status": "ready", "count": count}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def get_article_content(output_dir: Path, slug: str) -> dict[str, Any]:
    db = _db_path(output_dir)
    if not db.exists():
        return {"status": "not_built"}
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as con:
            if not _table_exists(con, "article_intro"):
                return {"status": "not_built"}
            row = con.execute(
                "SELECT title, intro FROM article_intro WHERE slug = ?", (slug,)
            ).fetchone()
            if not row:
                return {"status": "not_found", "slug": slug}
            return {"status": "found", "slug": slug, "title": row[0], "intro": row[1]}
    except Exception as exc:
        logger.warning("wiki_article_graph_service: get_article_content error: %s", exc)
        return {"status": "error", "error": str(exc)}


def build_content_index(output_dir: Path, *, force: bool = False) -> None:
    """Scan details.jsonl and store the first text chunk per article in article_intro table."""
    key = str(output_dir)
    db = _db_path(output_dir)
    details_path = output_dir / "details.jsonl"

    if not db.exists():
        with _CONTENT_LOCK:
            _CONTENT_BUILD_STATUS[key] = {"status": "error", "error": "main index not built"}
        return
    if not details_path.exists():
        with _CONTENT_LOCK:
            _CONTENT_BUILD_STATUS[key] = {"status": "error", "error": "details.jsonl not found"}
        return

    with _CONTENT_LOCK:
        if _CONTENT_BUILD_STATUS.get(key, {}).get("status") == "building" and not force:
            return
        _CONTENT_BUILD_STATUS[key] = {"status": "building", "phase": "scanning", "count": 0}

    logger.info("wiki_article_graph_service: building article_intro from %s", details_path.name)
    try:
        with sqlite3.connect(str(db), timeout=300) as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            if force:
                con.execute("DROP TABLE IF EXISTS article_intro")
            con.execute("""
                CREATE TABLE IF NOT EXISTS article_intro (
                    slug TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    intro TEXT NOT NULL
                )
            """)

        seen: set[str] = set()
        buf: list[tuple[str, str, str]] = []
        total = 0
        BATCH = 5000

        def _flush() -> None:
            nonlocal total
            with sqlite3.connect(str(db), timeout=60) as _con:
                _con.execute("PRAGMA journal_mode=WAL")
                _con.execute("BEGIN")
                _con.executemany("INSERT OR IGNORE INTO article_intro(slug, title, intro) VALUES (?,?,?)", buf)
                _con.execute("COMMIT")
            total += len(buf)
            buf.clear()
            with _CONTENT_LOCK:
                _CONTENT_BUILD_STATUS[key] = {"status": "building", "phase": "scanning", "count": total}

        with details_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("kind") != "wiki_detail":
                    continue
                article_id = str(obj.get("wiki_article_id") or "")
                slug = article_id[len("article:"):] if article_id.startswith("article:") else article_id
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                title = str(obj.get("article_title") or slug)
                section = str(obj.get("section_title") or "")
                content = str(obj.get("content") or "")[:1800]
                intro = f"{section}\n\n{content}".strip() if section else content
                buf.append((slug, title, intro))
                if len(buf) >= BATCH:
                    _flush()

        if buf:
            _flush()

        logger.info("wiki_article_graph_service: article_intro done — %d articles", total)
        with _CONTENT_LOCK:
            _CONTENT_BUILD_STATUS[key] = {"status": "ready", "count": total}
    except Exception as exc:
        logger.error("wiki_article_graph_service: build_content_index error: %s", exc)
        with _CONTENT_LOCK:
            _CONTENT_BUILD_STATUS[key] = {"status": "error", "error": str(exc)}


# ── Internal Build Functions ──────────────────────────────────────────────────

def _build_hubs(output_dir: Path, status_key: str) -> None:
    """Build hubs table: top articles by incoming link count."""
    db = _db_path(output_dir)
    _set_domain_status(status_key, {"status": "building", "phase": "computing_in_degree"})
    logger.info("wiki_article_graph_service: building hubs table")
    with sqlite3.connect(str(db), timeout=60) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("DROP TABLE IF EXISTS hubs")
        con.execute("""
            CREATE TABLE hubs (
                rank INTEGER NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                in_degree INTEGER NOT NULL
            )
        """)
        con.execute("""
            INSERT INTO hubs(rank, slug, title, in_degree)
            SELECT ROW_NUMBER() OVER (ORDER BY cnt DESC) as rank,
                   e.to_slug,
                   COALESCE(a.title, e.to_slug) as title,
                   e.cnt as in_degree
            FROM (
                SELECT to_slug, COUNT(*) as cnt
                FROM edges
                GROUP BY to_slug
                ORDER BY cnt DESC
                LIMIT 100
            ) e
            LEFT JOIN articles a ON a.slug = e.to_slug
        """)
        count = con.execute("SELECT COUNT(*) FROM hubs").fetchone()[0]
    logger.info("wiki_article_graph_service: hubs table built with %d entries", count)
    _set_domain_status(status_key, {"status": "ready", "count": count})


def _slug_normalize(name: str) -> str:
    """Normalize a Wikipedia title/category name to the slug format used in articles table."""
    # Replace spaces with underscores, lowercase first char like MediaWiki does
    s = name.strip().replace(" ", "_")
    if s:
        s = s[0].upper() + s[1:]
    return s


def _build_categories(output_dir: Path, status_key: str, corpus_path: Path) -> None:
    """Stream BZ2 dump, extract Kategorie links, store in article_categories and top_categories."""
    db = _db_path(output_dir)
    logger.info("wiki_article_graph_service: building categories from %s", corpus_path)
    _set_domain_status(status_key, {"status": "building", "phase": "streaming_bz2", "articles_processed": 0})

    # Load known slugs into a set for fast membership check
    _set_domain_status(status_key, {"status": "building", "phase": "loading_slugs"})
    with sqlite3.connect(str(db), timeout=60) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("DROP TABLE IF EXISTS article_categories")
        con.execute("DROP TABLE IF EXISTS top_categories")
        con.execute("""
            CREATE TABLE article_categories (
                slug TEXT NOT NULL,
                category TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ac_slug ON article_categories(slug)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ac_cat ON article_categories(category)")
        known_slugs: set[str] = set(
            row[0] for row in con.execute("SELECT slug FROM articles").fetchall()
        )

    logger.info("wiki_article_graph_service: loaded %d known slugs", len(known_slugs))
    _set_domain_status(status_key, {"status": "building", "phase": "streaming_bz2", "known_slugs": len(known_slugs)})

    title_re = re.compile(r"<title>(.*?)</title>")
    cat_re = re.compile(r"\[\[Kategorie:([^\|\]]+)")

    current_title: str = ""
    current_slug: str = ""
    buf: list[tuple[str, str]] = []
    articles_processed = 0
    cat_pairs = 0
    BATCH = 5000

    def _flush(con_inner: sqlite3.Connection) -> None:
        nonlocal cat_pairs
        if buf:
            con_inner.executemany(
                "INSERT INTO article_categories(slug, category) VALUES (?,?)", buf
            )
            cat_pairs += len(buf)
            buf.clear()

    with sqlite3.connect(str(db), timeout=300) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        with bz2.open(str(corpus_path), "rt", encoding="utf-8", errors="replace") as fh:
            in_text = False
            text_buf: list[str] = []
            for line in fh:
                if "<title>" in line:
                    m = title_re.search(line)
                    if m:
                        current_title = m.group(1)
                        current_slug = _slug_normalize(current_title)
                        in_text = False
                        text_buf = []
                elif "<text" in line:
                    in_text = True
                    text_buf.append(line)
                elif "</text>" in line:
                    if in_text:
                        text_buf.append(line)
                        full_text = "".join(text_buf)
                        if current_slug in known_slugs:
                            for m in cat_re.finditer(full_text):
                                cat_name = m.group(1).strip()
                                if cat_name:
                                    buf.append((current_slug, cat_name))
                        articles_processed += 1
                        if articles_processed % 100_000 == 0:
                            con.execute("BEGIN")
                            _flush(con)
                            con.execute("COMMIT")
                            _set_domain_status(status_key, {
                                "status": "building", "phase": "streaming_bz2",
                                "articles_processed": articles_processed,
                                "category_pairs": cat_pairs,
                            })
                            logger.info("wiki_article_graph_service: categories %d articles, %d pairs", articles_processed, cat_pairs)
                        in_text = False
                        text_buf = []
                elif in_text:
                    text_buf.append(line)

            # Final flush
            con.execute("BEGIN")
            _flush(con)
            con.execute("COMMIT")

    # Build top_categories
    _set_domain_status(status_key, {"status": "building", "phase": "aggregating"})
    with sqlite3.connect(str(db), timeout=120) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE top_categories AS
            SELECT category, COUNT(*) as article_count
            FROM article_categories
            GROUP BY category
            HAVING COUNT(*) >= 5
            ORDER BY article_count DESC
        """)
        con.execute("CREATE UNIQUE INDEX idx_top_cat ON top_categories(category)")
        top_count = con.execute("SELECT COUNT(*) FROM top_categories").fetchone()[0]

    logger.info("wiki_article_graph_service: categories done — %d top categories", top_count)
    _set_domain_status(status_key, {"status": "ready", "count": top_count, "category_pairs": cat_pairs})


def _build_clusters(output_dir: Path, status_key: str) -> None:
    """Multi-source BFS from top 40 hub articles, assign each article to a cluster."""
    db = _db_path(output_dir)
    logger.info("wiki_article_graph_service: building clusters")
    _set_domain_status(status_key, {"status": "building", "phase": "loading_hubs"})

    with sqlite3.connect(str(db), timeout=60) as con:
        hub_rows = con.execute(
            "SELECT slug, title FROM hubs ORDER BY rank ASC LIMIT 40"
        ).fetchall()

    if not hub_rows:
        _set_domain_status(status_key, {"status": "error", "error": "hubs table empty — build hubs first"})
        return

    hub_slugs = [r[0] for r in hub_rows]
    hub_titles = {r[0]: r[1] for r in hub_rows}

    # assignment: slug -> (hub_slug, hub_title, hop_distance)
    assignment: dict[str, tuple[str, str, int]] = {}
    for slug in hub_slugs:
        assignment[slug] = (slug, hub_titles[slug], 0)

    frontier: set[str] = set(hub_slugs)
    hop = 0
    BATCH_SIZE = 5000
    total_assigned = len(frontier)

    logger.info("wiki_article_graph_service: BFS starting with %d seeds", len(frontier))

    with sqlite3.connect(str(db), timeout=600) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=-131072")

        while frontier:
            hop += 1
            _set_domain_status(status_key, {
                "status": "building", "phase": "bfs",
                "hop": hop, "frontier_size": len(frontier),
                "total_assigned": total_assigned,
            })
            if hop % 5 == 0:
                logger.info("wiki_article_graph_service: BFS hop %d, frontier=%d, assigned=%d",
                            hop, len(frontier), total_assigned)

            frontier_list = list(frontier)
            next_frontier: set[str] = set()

            for i in range(0, len(frontier_list), BATCH_SIZE):
                batch = frontier_list[i:i + BATCH_SIZE]
                placeholders = ",".join("?" * len(batch))
                rows = con.execute(
                    f"SELECT from_slug, to_slug FROM edges WHERE from_slug IN ({placeholders})",
                    batch,
                ).fetchall()
                for from_slug, to_slug in rows:
                    if to_slug not in assignment:
                        # Inherit hub from the parent
                        parent_hub, parent_title, _ = assignment[from_slug]
                        assignment[to_slug] = (parent_hub, parent_title, hop)
                        next_frontier.add(to_slug)
                        total_assigned += 1

            frontier = next_frontier

    # Insert results
    _set_domain_status(status_key, {"status": "building", "phase": "inserting", "total_assigned": total_assigned})
    logger.info("wiki_article_graph_service: BFS done — %d articles assigned", total_assigned)

    with sqlite3.connect(str(db), timeout=300) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("DROP TABLE IF EXISTS article_clusters")
        con.execute("DROP TABLE IF EXISTS top_clusters")
        con.execute("""
            CREATE TABLE article_clusters (
                slug TEXT PRIMARY KEY,
                hub_slug TEXT NOT NULL,
                hub_title TEXT NOT NULL,
                hop_distance INTEGER NOT NULL
            )
        """)
        BATCH = 10000
        items = list(assignment.items())
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            con.execute("BEGIN")
            con.executemany(
                "INSERT OR REPLACE INTO article_clusters(slug, hub_slug, hub_title, hop_distance) VALUES (?,?,?,?)",
                [(slug, hub_slug, hub_title, hop_dist) for slug, (hub_slug, hub_title, hop_dist) in chunk],
            )
            con.execute("COMMIT")

        con.execute("""
            CREATE TABLE top_clusters AS
            SELECT hub_slug, hub_title, COUNT(*) as article_count
            FROM article_clusters
            GROUP BY hub_slug
            ORDER BY article_count DESC
        """)
        con.execute("CREATE UNIQUE INDEX idx_top_cl ON top_clusters(hub_slug)")
        top_count = con.execute("SELECT COUNT(*) FROM top_clusters").fetchone()[0]

    logger.info("wiki_article_graph_service: clusters done — %d clusters", top_count)
    _set_domain_status(status_key, {"status": "ready", "count": top_count, "total_assigned": total_assigned})

"""Read-only inventory of on-disk wiki import corpora, checkpoints and outputs."""

from __future__ import annotations

from pathlib import Path


def collect_wiki_disk_state(data_dir: Path) -> dict:
    """Return the real on-disk state of wiki import files, independent of jobs."""
    wiki_dir = data_dir / "wiki_corpora"
    checkpoint_dir = data_dir / "wiki_checkpoints"

    def _file_info(p: Path) -> dict | None:
        if not p.exists():
            return None
        stat = p.stat()
        return {"path": p.name, "size_bytes": stat.st_size, "mtime": stat.st_mtime}

    files = []
    if wiki_dir.exists():
        for f in sorted(wiki_dir.iterdir()):
            info = _file_info(f)
            if info is None:
                continue
            name = f.name
            if name.endswith(".partial.chunks_cache.json"):
                kind = "chunks_cache"
            elif name.endswith(".partial.links.jsonl"):
                kind = "partial_links_jsonl"
            elif name.endswith(".links.jsonl"):
                kind = "links_jsonl"
            elif name.endswith(".partial.jsonl"):
                kind = "partial_jsonl"
            elif name.endswith(".compact.jsonl"):
                kind = "compact_jsonl"
            elif name.endswith(".normalized.jsonl"):
                kind = "normalized_jsonl"
            elif name.endswith(".xml.bz2") or name.endswith(".xml.gz"):
                kind = "corpus_compressed"
            elif name.endswith(".xml"):
                kind = "corpus_xml"
            elif name.endswith(".txt.bz2"):
                kind = "index_compressed"
            elif name.endswith(".txt"):
                kind = "index_txt"
            else:
                kind = "other"
            files.append({**info, "kind": kind})

    # CodeCompass output files from knowledge_indices/wiki/
    cc_outputs: list[dict] = []
    ki_wiki_dir = data_dir / "knowledge_indices" / "wiki"
    if ki_wiki_dir.exists():
        import json as _json_cc
        for index_dir in sorted(ki_wiki_dir.iterdir()):
            if not index_dir.is_dir():
                continue
            for run_dir in sorted(index_dir.iterdir(), reverse=True):
                if not run_dir.is_dir():
                    continue
                run_files = {}
                manifest = {}
                for rf in sorted(run_dir.iterdir()):
                    if rf.name == "manifest.json":
                        try:
                            manifest = _json_cc.loads(rf.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                        continue
                    info = _file_info(rf)
                    if info:
                        run_files[rf.name] = info
                if run_files:
                    cc_outputs.append({
                        "index_id": index_dir.name,
                        "run_id": run_dir.name,
                        "files": run_files,
                        "manifest": {
                            "index_record_count": manifest.get("index_record_count"),
                            "node_count": manifest.get("node_count"),
                            "relation_record_count": manifest.get("relation_record_count"),
                            "link_edge_count": manifest.get("link_edge_count"),
                            "profile_name": manifest.get("profile_name"),
                            "generated_at": manifest.get("generated_at"),
                        } if manifest else None,
                    })

    checkpoints = []
    if checkpoint_dir.exists():
        import json as _json
        for f in sorted(checkpoint_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                checkpoints.append({"file": f.name, **data})
            except Exception:
                pass

    return {"files": files, "checkpoints": checkpoints, "codecompass_outputs": cc_outputs}

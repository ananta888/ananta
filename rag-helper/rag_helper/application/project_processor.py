from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from rag_helper.extractors.base import JavaLikeExtractor
from rag_helper.filesystem.text_reader import read_text_file
from rag_helper.utils.ids import sha1_text


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, items) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def collect_files(root: Path, extensions: set[str], excludes: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in excludes for part in p.parts):
            continue
        ext = p.suffix.lower().lstrip(".")
        if ext in extensions:
            files.append(p)
    return sorted(files)


def build_package_type_index(
    files: list[Path],
    root: Path,
    java_extractor: JavaLikeExtractor,
) -> tuple[dict[str, set[str]], list[dict]]:
    known_package_types: dict[str, set[str]] = defaultdict(set)
    scan_errors: list[dict] = []
    for path in files:
        if path.suffix.lower() != ".java":
            continue
        rel_path = str(path.relative_to(root))
        text = read_text_file(path)
        if text is None:
            continue
        try:
            scan = java_extractor.pre_scan_types(rel_path, text)
            if scan["package"]:
                for tn in scan["type_names"]:
                    known_package_types[scan["package"]].add(tn)
        except Exception as exc:
            scan_errors.append({
                "file": rel_path,
                "ext": "java",
                "stage": "pre_scan",
                "error": str(exc),
            })
    return dict(known_package_types), scan_errors


def process_project(
    root: Path,
    out_dir: Path,
    extensions: set[str],
    excludes: set[str],
    include_code_snippets: bool,
    exclude_trivial_methods: bool,
    include_xml_node_details: bool,
    java_extractor_cls,
    xml_extractor_cls,
    xsd_extractor_cls,
) -> None:
    ensure_dir(out_dir)

    java_extractor = java_extractor_cls(
        include_code_snippets=include_code_snippets,
        exclude_trivial_methods=exclude_trivial_methods,
    )
    extractors = {
        "java": java_extractor,
        "xml": xml_extractor_cls(include_xml_node_details=include_xml_node_details),
        "xsd": xsd_extractor_cls(),
    }

    files = collect_files(root, extensions, excludes)
    known_package_types, pre_scan_errors = build_package_type_index(files, root, java_extractor)

    all_index: list[dict] = []
    all_details: list[dict] = []
    all_relations: list[dict] = []
    manifest_files: list[dict] = list(pre_scan_errors)

    for path in files:
        rel_path = str(path.relative_to(root))
        ext = path.suffix.lower().lstrip(".")
        text = read_text_file(path)
        if text is None:
            manifest_files.append({"file": rel_path, "ext": ext, "error": "unreadable"})
            continue

        try:
            extractor = extractors.get(ext)
            if extractor is None:
                continue

            if ext == "java":
                idx, det, rel, stats = extractor.parse(
                    rel_path=rel_path,
                    text=text,
                    known_package_types=known_package_types,
                )
            else:
                idx, det, rel, stats = extractor.parse(rel_path, text)

            all_index.extend(idx)
            all_details.extend(det)
            all_relations.extend(rel)

            manifest_files.append({
                "file": rel_path,
                "ext": ext,
                "sha1": sha1_text(text),
                "size": len(text.encode("utf-8", errors="ignore")),
                "stats": stats,
            })
        except Exception as e:
            manifest_files.append({
                "file": rel_path,
                "ext": ext,
                "error": str(e),
            })

    manifest = {
        "project_root": str(root),
        "file_count": len(manifest_files),
        "index_record_count": len(all_index),
        "detail_record_count": len(all_details),
        "relation_record_count": len(all_relations),
        "options": {
            "include_code_snippets": include_code_snippets,
            "exclude_trivial_methods": exclude_trivial_methods,
            "include_xml_node_details": include_xml_node_details,
        },
        "package_type_index": {k: sorted(v) for k, v in known_package_types.items()},
        "files": manifest_files,
    }

    write_jsonl(out_dir / "index.jsonl", all_index)
    write_jsonl(out_dir / "details.jsonl", all_details)
    write_jsonl(out_dir / "relations.jsonl", all_relations)
    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Fertig: {out_dir}")
    print(f"Dateien: {len(manifest_files)}")
    print(f"Index Records: {len(all_index)}")
    print(f"Detail Records: {len(all_details)}")
    print(f"Relation Records: {len(all_relations)}")

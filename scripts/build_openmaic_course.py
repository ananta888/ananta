#!/usr/bin/env python3
"""Build deterministic OpenMAIC and offline artifacts for the Ananta course."""

# ruff: noqa: E501 -- embedded deterministic HTML/CSS/JS is intentionally kept literal

from __future__ import annotations

import argparse
import html
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = ROOT / "docs/learning/courses/openmaic-ananta-codecompass"
CONTENT_PATH = COURSE_ROOT / "openmaic-content.json"
SOURCE_PATH = COURSE_ROOT / "source-audit.json"
SNAPSHOT_PATH = COURSE_ROOT / "demo/codecompass-snapshot.json"
ARCHIVE_PATH = COURSE_ROOT / "openmaic-ananta-codecompass.maic.zip"
OFFLINE_PATH = COURSE_ROOT / "offline/index.html"
OPENMAIC_VERSION = "1.0.0"
OPENMAIC_FORMAT_VERSION = 1
FIXED_EPOCH_MS = 1_788_134_400_000


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"course_input_not_object:{path.name}")
    return value


def _text_element(identifier: str, content: str, *, top: int, height: int, title: bool = False) -> dict[str, Any]:
    return {
        "id": identifier,
        "type": "text",
        "left": 70,
        "top": top,
        "width": 860,
        "height": height,
        "rotate": 0,
        "content": content,
        "defaultFontName": "Arial",
        "defaultColor": "#10233f" if title else "#24364b",
        "lineHeight": 1.35,
        "textType": "title" if title else "content",
    }


def _slide(scene: Mapping[str, Any], order: int, revision: str) -> dict[str, Any]:
    title = html.escape(str(scene["title"]))
    bullets = "".join(f"<li>{html.escape(str(item))}</li>" for item in scene.get("bullets", []))
    notes = str(scene.get("speaker_notes") or "")
    slide_id = str(scene["id"])
    return {
        "type": "slide",
        "title": str(scene["title"]),
        "order": order,
        "content": {
            "type": "slide",
            "schemaVersion": 1,
            "canvas": {
                "id": f"slide-{slide_id}",
                "viewportSize": 1000,
                "viewportRatio": 1.7777777778,
                "theme": {
                    "backgroundColor": "#f7fafc",
                    "themeColors": ["#1f6feb", "#0f766e", "#f59e0b"],
                    "fontColor": "#24364b",
                    "fontName": "Arial",
                },
                "background": {"type": "solid", "color": "#f7fafc"},
                "elements": [
                    _text_element(f"{slide_id}-title", f"<h1>{title}</h1>", top=55, height=110, title=True),
                    _text_element(f"{slide_id}-body", f"<ul>{bullets}</ul>", top=185, height=300),
                    _text_element(
                        f"{slide_id}-revision",
                        f"<p>Snapshot-Revision: {html.escape(revision)} · unverified_missing_SRC_ids</p>",
                        top=500,
                        height=45,
                    ),
                ],
                "type": "content",
                "script": notes,
            },
        },
    }


def _quiz(interaction: Mapping[str, Any], order: int) -> dict[str, Any]:
    return {
        "type": "quiz",
        "title": str(interaction["title"]),
        "order": order,
        "content": {
            "type": "quiz",
            "questions": list(interaction.get("questions") or []),
        },
    }


def _demo_slide(question: Mapping[str, Any], order: int, revision: str) -> dict[str, Any]:
    paths = [str(item.get("path") or "") for item in question.get("evidence") or []]
    return _slide(
        {
            "id": f"demo-{question['id']}",
            "title": f"Snapshot-Frage: {question['question']}",
            "bullets": [
                f"Schwache Antwort: {question['weak_retrieval_answer']}",
                f"Evidenzansicht: {question['evidence_answer']}",
                f"Sichtbare Pfade: {' · '.join(paths)}",
            ],
            "speaker_notes": "Gespeicherter Offline-Snapshot; keine Live- oder Grounding-Behauptung.",
        },
        order,
        revision,
    )


def build_manifest(
    content: Mapping[str, Any],
    source: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    revision = str(source["repository_revision"])
    slides = list(content.get("slides") or [])
    interactions = list(content.get("interactions") or [])
    if len(slides) < 5 or len(interactions) < 2:
        raise ValueError("course_content_incomplete")
    demo_questions = list(snapshot.get("questions") or [])
    if len(demo_questions) < 3:
        raise ValueError("course_demo_questions_incomplete")
    scenes = [
        _slide(slides[0], 0, revision),
        _slide(slides[1], 1, revision),
        _quiz(interactions[0], 2),
        _slide(slides[2], 3, revision),
        *(_demo_slide(item, 4 + index, revision) for index, item in enumerate(demo_questions[:3])),
        _quiz(interactions[1], 7),
        _slide(slides[3], 8, revision),
        _slide(slides[4], 9, revision),
    ]
    return {
        "formatVersion": OPENMAIC_FORMAT_VERSION,
        "exportedAt": "2026-08-31T00:00:00.000Z",
        "appVersion": OPENMAIC_VERSION,
        "stage": {
            "name": str(content["title"]),
            "description": str(content["subtitle"]),
            "language": "de-DE",
            "style": "Ananta evidence-bound offline course",
            "createdAt": FIXED_EPOCH_MS,
            "updatedAt": FIXED_EPOCH_MS,
        },
        "agents": [
            {
                "name": "Kursmoderation",
                "role": "teacher",
                "persona": "Erklärt präzise, trennt Implementierung, Konzept und unverified Evidenz.",
                "avatar": "",
                "color": "#1f6feb",
                "priority": 1,
            }
        ],
        "scenes": scenes,
        "mediaIndex": {},
    }


def build_archive(manifest: Mapping[str, Any]) -> bytes:
    encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        entry = ZipInfo("manifest.json", date_time=(2026, 8, 31, 0, 0, 0))
        entry.compress_type = ZIP_DEFLATED
        entry.external_attr = 0o100644 << 16
        archive.writestr(entry, encoded, compress_type=ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def build_offline_html(
    content: Mapping[str, Any], source: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> bytes:
    data = json.dumps(
        {"content": content, "source": source, "snapshot": snapshot},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ananta und CodeCompass — Offline-Kurs</title>
<style>
body{{font:18px system-ui,sans-serif;margin:0;background:#eef3f8;color:#172b45}}main{{max-width:960px;margin:auto;padding:24px}}
.banner{{background:#7c2d12;color:white;padding:12px;border-radius:8px}}section{{background:white;margin:18px 0;padding:22px;border-radius:12px}}
button{{padding:10px 16px;margin:6px;border:1px solid #1f6feb;border-radius:6px;background:white}}button:hover{{background:#eaf2ff}}
.ok{{color:#047857}}.bad{{color:#b91c1c}}code{{overflow-wrap:anywhere}}li{{margin:8px 0}}
</style></head><body><main>
<div class="banner">OFFLINE SNAPSHOT — unverified_missing_SRC_ids — keine Live-Systeme</div>
<h1>Ananta und CodeCompass</h1><p>Von der Entwicklungsfrage zur belegten Änderung</p>
<div id="course"></div><div id="demo"></div><div id="quiz"></div>
<script id="course-data" type="application/json">{data}</script>
<script>
const data=JSON.parse(document.getElementById('course-data').textContent);
const el=(tag,text)=>{{const n=document.createElement(tag);n.textContent=text;return n}};
for(const slide of data.content.slides){{const s=document.createElement('section');s.append(el('h2',slide.title));const u=document.createElement('ul');for(const b of slide.bullets)u.append(el('li',b));s.append(u);document.getElementById('course').append(s)}}
const d=document.getElementById('demo');d.append(el('h2','Drei gespeicherte CodeCompass-Fragen'));
for(const q of data.snapshot.questions){{const s=document.createElement('section');s.append(el('h3',q.question),el('p',q.evidence_answer),el('code',data.snapshot.repository_revision+' · '+q.evidence.map(x=>x.path).join(' · ')));d.append(s)}}
const qroot=document.getElementById('quiz');qroot.append(el('h2','Interaktionen'));
for(const interaction of data.content.interactions)for(const q of interaction.questions){{const s=document.createElement('section');s.append(el('h3',q.question));for(const o of q.options){{const b=el('button',o.value+' '+o.label);b.onclick=()=>{{const correct=q.answer.includes(o.value);const r=el('p',correct?'Richtig: '+q.analysis:'Nicht ausreichend: '+q.analysis);r.className=correct?'ok':'bad';s.append(r)}};s.append(b)}}qroot.append(s)}}
</script></main></body></html>
"""
    return document.encode("utf-8")


def expected_artifacts() -> dict[Path, bytes]:
    content = _load(CONTENT_PATH)
    source = _load(SOURCE_PATH)
    snapshot = _load(SNAPSHOT_PATH)
    manifest = build_manifest(content, source, snapshot)
    return {
        ARCHIVE_PATH: build_archive(manifest),
        OFFLINE_PATH: build_offline_html(content, source, snapshot),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    artifacts = expected_artifacts()
    if args.check:
        mismatches = [str(path.relative_to(ROOT)) for path, value in artifacts.items() if not path.is_file() or path.read_bytes() != value]
        if mismatches:
            print(json.dumps({"status": "failed", "reason_code": "generated_course_outdated", "paths": mismatches}))
            return 1
        print(json.dumps({"status": "passed", "artifacts": len(artifacts)}))
        return 0
    for path, value in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    print(json.dumps({"status": "written", "artifacts": len(artifacts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""E2E test: TUI AI-Snake-Chat propose flow with local LMStudio.

Testet den vollständigen Informationsfluss:

1. LMStudio-Konnektivität (Probe)
2. ChatPromptBuilder – welche Informationen fliessen in den Prompt?
   (active_target, rolling_summary, recent_turns, codecompass, rag, runtime_status)
3. snake/ask propose flow – Frage → Hub → Worker/Hub-Direct → LMStudio → Antwort
4. TUI-Cast-Aufnahme – die Interaktion wird als .cast (asciinema v2) aufgezeichnet
5. Cast-Verifikation – enthält Frage, Antwort, Flow-Marker

Informationen, die der Worker/LLM zur Antwortfindung erhält:
- Status-Delta (mode, focus, section, selected_index, panel_states)
- CodeCompass-Hinweise (relevante Dateien/Symbole)
- RAG-Kontext (zusätzliche Referenzen)
- Artifact-Overlay (aktuelles Ziel, Pfad, Excerpt)
- Gesprächshistorie (rolling_summary, recent_turns)
- Depth-Instruction (overview/deep/expert)

Run with:
    ANANTA_E2E_LIVE_LMSTUDIO=1 pytest tests/e2e/test_tui_snake_chat_propose_flow_e2e.py -v

Optional:
    ANANTA_TUI_LLM_API_BASE=http://localhost:1234/v1
    ANANTA_TUI_LLM_MODEL=meta-llama_-_llama-3.2-1b-instruct
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from client_surfaces.operator_tui.chat_memory import ChatMemoryContext, MemoryTurn
from client_surfaces.operator_tui.chat_prompt_builder import ChatPromptBuilder

from scripts.e2e.record_tui_demo import record_tui_demo

ROOT = Path(__file__).resolve().parents[2]
TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUE_VALUES


def _lmstudio_api_base() -> str:
    return str(
        os.environ.get("ANANTA_TUI_LLM_API_BASE")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or os.environ.get("LMSTUDIO_URL")
        or "http://127.0.0.1:1234/v1"
    ).rstrip("/")


def _lmstudio_model() -> str:
    return str(
        os.environ.get("ANANTA_TUI_LLM_MODEL")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL")
        or "meta-llama_-_llama-3.2-1b-instruct"
    )


def _require_live_lmstudio() -> tuple[str, str]:
    """Skip unless ANANTA_E2E_LIVE_LMSTUDIO=1 and LMStudio reachable.
    Returns (api_base, model).
    """
    if not (_env_flag("ANANTA_E2E_LIVE_TUI_LLM") or _env_flag("ANANTA_E2E_LIVE_LMSTUDIO")):
        pytest.skip(
            "Set ANANTA_E2E_LIVE_TUI_LLM=1 or ANANTA_E2E_LIVE_LMSTUDIO=1 "
            "to run the live TUI AI-Snake propose flow E2E."
        )
    api_base = _lmstudio_api_base()
    try:
        with urllib.request.urlopen(f"{api_base}/models", timeout=2.5):
            pass
    except (urllib.error.URLError, TimeoutError):
        pytest.skip(f"LMStudio API not reachable at {api_base}")
    return api_base, _lmstudio_model()


def _probe_lmstudio_chat(api_base: str, model: str) -> str:
    """Send a minimal chat request to verify LMStudio is responsive."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Antworte kurz: online"}],
        "temperature": 0.0,
        "max_tokens": 24,
    }
    request = urllib.request.Request(
        url=f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8.0) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    assert isinstance(choices, list) and choices, f"LMStudio returned no choices: {raw[:400]}"
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    assert isinstance(message, dict), f"No message in LMStudio response: {raw[:400]}"
    content = str(message.get("content") or "").strip()
    assert content, f"LMStudio response content is empty: {raw[:400]}"
    return content


def _resolve_ref(ref: str) -> Path:
    ref_path = Path(ref)
    return ref_path if ref_path.is_absolute() else ROOT / ref_path


# ── Test 1: LMStudio-Konnektivität ──────────────────────────────────────

def test_lmstudio_connectivity_probe() -> None:
    """Test 1: LMStudio muss erreichbar sein und auf Chat-Requests antworten."""
    api_base, model = _require_live_lmstudio()
    reply = _probe_lmstudio_chat(api_base, model)
    assert reply, "LMStudio chat probe returned empty reply"
    print(f"\n  LMStudio ok: api_base={api_base} model={model}")
    print(f"  Probe reply: {reply}")


# ── Test 2: ChatPromptBuilder Informationsfluss ─────────────────────────

def _build_test_memory() -> ChatMemoryContext:
    """Erzeugt einen ChatMemoryContext mit realistischen Testdaten,
    die simulieren, was die TUI an Informationen sammelt.
    """
    return ChatMemoryContext(
        recent_turns=[
            MemoryTurn(role="user", content="Was zeigt der Menüpunkt Goals?"),
            MemoryTurn(role="assistant", content="Goals zeigt Ziele, Status und Priorität."),
            MemoryTurn(role="user", content="Wie priorisiere ich schnell?"),
        ],
        rolling_summary=(
            "User hat nach Goals gefragt. Assistant hat Ziele, Status "
            "und Priorität erklärt. User fragt nun nach Priorisierung."
        ),
        active_target_excerpt=(
            "active goal: Improve TUI onboarding (status=active)\n"
            "ready goal: Explain AI-snake guidance (status=ready)"
        ),
        codecompass_refs=[
            "client_surfaces/operator_tui/chat_prompt_builder.py :: ChatPromptBuilder.build",
            "client_surfaces/operator_tui/chat_memory.py :: ChatMemoryContext",
            "client_surfaces/operator_tui/tutorial_ai_mixin.py :: _tutorial_ai_tip_sync",
        ],
        rag_snippets=[
            "ChatPromptBuilder assembliert context sections in budget-priority order",
            "Der worker-v2-payload enthaelt question, context, depth, memory_context",
        ],
        runtime_status="mode=snake focus=nav section=goals idx=1",
        metadata={"memory_version": "v2", "manifest_hash": "abc123"},
    )


def test_chat_prompt_builder_context_assembly() -> None:
    """Test 2: Zeigt, welche Informationen der ChatPromptBuilder
    in den Prompt einbaut – und wie die Context-Budget-Policy greift.
    """
    memory = _build_test_memory()
    builder = ChatPromptBuilder(
        question="Wie priorisiere ich schnell?",
        depth="deep",
        memory=memory,
        context_budget=3000,
        max_turns_chars=1800,
    )
    result = builder.build()

    print("\n  === ChatPromptBuilder Informationsfluss ===")
    print(f"  included_sections: {result.included_sections}")
    print(f"  total_chars: {result.total_chars}")
    print(f"  messages count: {len(result.messages)}")
    print(f"  system message chars: {len(result.messages[0]['content']) if result.messages else 0}")

    # Alle relevanten Sektionen müssen im Prompt sein
    assert "active_target" in result.included_sections, "active_target section missing"
    assert "rolling_summary" in result.included_sections, "rolling_summary section missing"
    assert "recent_turns" in result.included_sections, "recent_turns section missing"
    assert "codecompass" in result.included_sections, "codecompass section missing"
    assert "rag" in result.included_sections, "rag section missing"
    assert "runtime_status" in result.included_sections, "runtime_status section missing"

    # Worker v2 payload muss strukturierte Daten enthalten
    v2 = result.worker_v2_payload
    assert v2["question"] == "Wie priorisiere ich schnell?"
    assert "memory_context" in v2
    assert "recent_turns" in v2["memory_context"]
    assert "rolling_summary" in v2["memory_context"]
    assert "codecompass_refs" in v2["memory_context"]
    assert len(v2["memory_context"]["recent_turns"]) == 3

    print("  ✓ Alle Sektionen im Prompt enthalten")
    print("  ✓ Worker v2 payload korrekt strukturiert")


# ── Test 3: Vollständiger snake/ask propose flow ────────────────────────

def _build_grounded_prompt() -> str:
    """Baut einen Prompt, wie er vom ChatPromptBuilder + CodeCompass + RAG
    zusammengestellt wird – also genau das, was der Worker/Hub bekommt.
    """
    memory = _build_test_memory()
    builder = ChatPromptBuilder(
        question="Wie priorisiere ich schnell im Goals-Bereich?",
        depth="deep",
        memory=memory,
        context_budget=3000,
    )
    result = builder.build()
    return result.prompt_text


def test_snake_ask_propose_flow_with_lmstudio(monkeypatch) -> None:
    """Test 3: Sendet eine Frage via ChatPromptBuilder an LMStudio.

    Dies simuliert den snake/ask propose flow:
    TUI → ChatPromptBuilder (context assembly) → Hub snake/ask → LMStudio

    Der Test zeigt:
    - Welcher Prompt gebaut wird (context sections)
    - Dass LMStudio eine Antwort generiert
    - Dass die Antwort sinnvoll ist und Bezug zur Frage nimmt
    """
    api_base, model = _require_live_lmstudio()
    _probe_lmstudio_chat(api_base, model)

    # 1. Prompt bauen (wie ChatPromptBuilder)
    memory = _build_test_memory()
    builder = ChatPromptBuilder(
        question="Wie priorisiere ich schnell im Goals-Bereich?",
        depth="deep",
        memory=memory,
        context_budget=3000,
    )
    result = builder.build()
    prompt_text = result.prompt_text

    print("\n  === snake/ask propose flow ===")
    print(f"  Prompt ({len(prompt_text)} chars):")
    for line in prompt_text.split("\n")[:15]:
        print(f"    {line}")
    if prompt_text.count("\n") > 15:
        print(f"    ... ({prompt_text.count('\n') - 15} more lines)")

    # 2. Sende an LMStudio (simuliert den Hub-Direct-Fallback)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Du bist AI-Snake im Ananta TUI. Du erklärst dem Nutzer "
                    "die TUI-Bedienung und Architektur. Antworte kurz und "
                    "konkret auf Deutsch. Nutze den gegebenen Kontext."
                ),
            },
            {"role": "user", "content": prompt_text},
        ],
        "temperature": 0.2,
        "max_tokens": 256,
    }
    request = urllib.request.Request(
        url=f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    assert isinstance(choices, list) and choices, f"No choices: {raw[:400]}"
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    answer = str(message.get("content") or "").strip()
    assert answer, "LMStudio returned empty answer"

    print(f"\n  Antwort ({len(answer)} chars):")
    print(f"    {answer[:500]}")

    # 3. Verifiziere: Antwort bezieht sich auf Goals/Priorisierung
    keywords_lower = answer.lower()
    has_goal_context = "goal" in keywords_lower or "ziel" in keywords_lower
    has_priority_context = "priorit" in keywords_lower or "active" in keywords_lower or "ready" in keywords_lower
    assert has_goal_context or has_priority_context, (
        f"Answer does not reference Goals/prioritization context. "
        f"Answer snippet: {answer[:200]}"
    )
    print("  ✓ Antwort referenziert Goals/Priorisierung-Kontext")


# ── Test 4: TUI-Cast-Aufnahme mit Vollständigem Flow ────────────────────

def test_tui_snake_chat_propose_flow_cast(monkeypatch) -> None:
    """Test 4: Zeichnet eine TUI-Session mit AI-Snake-Chat auf.

    Der Test erzeugt einen asciinema-v2 .cast file, der zeigt:
    1. TUI wird gestartet (mit Snake-Mode)
    2. Snake wird aktiviert
    3. Tutorial-AI wird eingeschaltet
    4. Chat-Backend wird auf ananta-worker gesetzt
    5. Frage wird via :ask gesendet
    6. Antwort wird angezeigt

    Der Cast wird auf folgende Marker geprüft:
    - [Ctrl+S] Snake (Snake-Mode aktiv)
    - ananta-worker (Chat-Backend)
    - /snake/ask (Propose-Endpunkt)
    - Frage im Klartext
    """
    api_base, model = _require_live_lmstudio()
    _probe_lmstudio_chat(api_base, model)

    # TUI-Umgebung setzen
    monkeypatch.setenv("ANANTA_TUI_LLM_API_BASE", api_base)
    monkeypatch.setenv("ANANTA_TUI_LLM_MODEL", model)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", api_base)
    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_MODEL", model)
    monkeypatch.setenv("ANANTA_TUI_CHAT_API_BASE_URL", api_base)
    monkeypatch.setenv("ANANTA_TUI_CHAT_MODEL", model)
    monkeypatch.setenv("ANANTA_TUI_CHAT_BACKEND", "ananta-worker")
    monkeypatch.setenv("ANANTA_TUI_SNAKE_TUTORIAL_AI", "1")
    monkeypatch.setenv("ANANTA_TUI_CHAT_ASK_TIMEOUT", "75")

    payload = record_tui_demo(
        run_id="test-snake-chat-propose-flow",
        flow_id="tui-snake-chat-propose-flow",
        enabled=True,
        scene="snake-mode-live-e2e",
        sync_targets=[],
    )

    assert payload["status"] == "recorded", f"record_tui_demo failed: {payload}"
    video_path = _resolve_ref(payload["video_ref"])
    assert video_path.exists(), f"Cast file not found: {video_path}"

    print(f"\n  Cast file: {video_path}")

    # Cast analysieren
    raw = video_path.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) >= 2, "Cast has fewer than 2 lines (header + at least 1 frame)"

    header = json.loads(lines[0])
    assert header["version"] == 2
    print(f"  Cast header: {header['width']}x{header['height']}, {len(lines) - 1} frames")

    # Plain-Text aus allen Frames extrahieren (ANSI-Sequenzen entfernen)
    frame_text = "\n".join(json.loads(line)[2] for line in lines[1:])
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.", "", frame_text)
    plain_lower = plain.lower()

    # Marker prüfen
    markers = {
        "Snake-Mode aktiv": "[Ctrl+S] Snake" in plain or "snake" in plain_lower,
        "Chat-Backend": "ananta-worker" in plain,
        "Propose-Endpunkt": "/snake/ask" in plain,
        "CodeCompass": "codecompass" in plain_lower,
        "Chat-Nachricht": "chat-nachricht" in plain_lower or "chat" in plain_lower,
        "Tutorial-AI": "tutorial" in plain_lower,
    }
    missing = [name for name, found in markers.items() if not found]
    if missing:
        print(f"  Fehlende Marker: {missing}")
        print(f"  Plain tail: {plain[-2000:]}")
    else:
        print("  ✓ Alle Marker gefunden")

    # Mindestens die Kern-Marker müssen vorhanden sein
    assert markers["Snake-Mode aktiv"], "Snake mode marker not found in cast"
    print("  ✓ Snake-Mode im Cast sichtbar")

    # ── Substantielle Verifikation: LLM-Antwortqualität ──────────
    # Beweist, dass der propose flow tatsächlich funktioniert:
    #   (a) LLM wurde gerufen und hat mit dem vom System-Prompt
    #       verlangten Marker geantwortet → Antwort ist in TUI sichtbar
    #   (b) Antwort enthält alle in der Frage geforderten
    #       Schlüsselkonzepte → Antwort ist inhaltlich sinnvoll
    #   (c) Antwort erscheint NACH der Frage im Cast
    #       → Anzeige-Reihenfolge in TUI ist korrekt

    # (a) LLM-Marker — der System-Prompt verlangt, dass die
    #     Antwort mit diesem Marker beginnt.
    llm_marker = "ANANTA-WORKER-CODECOMPASS-LMSTUDIO-CAST"
    assert llm_marker in plain, (
        f"LLM-Marker {llm_marker!r} nicht im Cast gefunden. "
        "Der System-Prompt verlangt diesen Marker als Präfix der Antwort. "
        f"Plain tail: {plain[-1500:]}"
    )
    print(f"  ✓ LLM-Marker im Cast gefunden")

    # (b) Schlüsselkonzepte — müssen in der ANTWORT erscheinen,
    #     nicht nur in der Frage. Wir suchen daher NACH dem
    #     LLM-Marker, damit die Frage selbst (die dieselben
    #     Wörter enthält) nicht fälschlich als Substanz gewertet
    #     wird.
    marker_idx = plain.find(llm_marker)
    answer_text = plain[marker_idx:]
    expected_concepts = [
        "CodeCompass",
        "chat_mixin.py",
        "/snake/ask",
        "ananta-worker",
        "LMStudio",
        "TUI",
    ]
    missing_concepts = [c for c in expected_concepts if c not in answer_text]
    assert not missing_concepts, (
        f"LLM-Antwort referenziert nicht alle Schlüsselkonzepte: "
        f"{missing_concepts}. "
        f"Antwort tail: {answer_text[:500]!r}"
    )
    print(
        f"  ✓ LLM-Antwort enthält alle "
        f"{len(expected_concepts)} Schlüsselkonzepte"
    )

    # (c) Anzeige-Reihenfolge — Antwort muss NACH der Frage
    #     erscheinen. Der Frage-Marker wird im Cast sichtbar,
    #     weil der User ihn tippt.
    question_marker = "Antworte exakt mit dem Marker"
    question_idx = plain.find(question_marker)
    assert question_idx >= 0, (
        f"Frage-Marker {question_marker!r} nicht im Cast. "
        f"Plain tail: {plain[-1500:]}"
    )
    assert marker_idx > question_idx, (
        f"LLM-Antwort erscheint vor oder gleichzeitig mit der Frage "
        f"(Frage an Pos. {question_idx}, Antwort an Pos. {marker_idx})"
    )
    print(
        f"  ✓ Antwort erscheint nach der Frage "
        f"(Δ = {marker_idx - question_idx} Zeichen)"
    )

    print("  ✓ TUI-Cast erfolgreich aufgezeichnet und verifiziert")


# ── Test 5: Vollständiger End-to-End-Diagnostik-Report ──────────────────

def test_snake_chat_propose_flow_diagnostics(monkeypatch) -> None:
    """Test 5: Gibt einen vollständigen Diagnostik-Report über den
    Informationsfluss im propose flow aus.

    Dieser Test dient als Dokumentation und zeigt:
    - Welche env vars gesetzt werden müssen
    - Welche Schritte der propose flow durchläuft
    - Welche Informationen an den Worker/LLM gehen
    - Welche Antwort zurückkommt
    """
    api_base, model = _require_live_lmstudio()

    print("\n" + "=" * 70)
    print("  DIAGNOSTIK: AI-Snake-Chat Propose Flow")
    print("=" * 70)

    # 1. LMStudio Info
    print(f"\n  [1] LMStudio")
    print(f"       API Base: {api_base}")
    print(f"       Model:    {model}")

    # 2. ChatPromptBuilder Info
    print(f"\n  [2] ChatPromptBuilder → Prompt-Zusammenstellung")
    memory = _build_test_memory()
    builder = ChatPromptBuilder(
        question="Wie priorisiere ich schnell?",
        depth="deep",
        memory=memory,
        context_budget=3000,
    )
    result = builder.build()
    print(f"       Question:       {result.worker_v2_payload['question']}")
    print(f"       Depth:          {result.worker_v2_payload['depth']}")
    print(f"       Sections:       {result.included_sections}")
    print(f"       Total chars:    {result.total_chars}")
    print(f"       Recent turns:   {len(result.worker_v2_payload['memory_context']['recent_turns'])}")
    print(f"       CodeCompass:    {len(result.worker_v2_payload['memory_context']['codecompass_refs'])} refs")
    print(f"       Rolling sum:    {len(result.worker_v2_payload['memory_context']['rolling_summary'])} chars")

    # 3. Propose Flow
    print(f"\n  [3] Propose Flow (Hub-Route)")
    print(f"       POST /snake/ask  ← TUI sendet Frage + Kontext")
    print(f"       → _worker_propose()  → Worker /step/propose")
    print(f"       → Fallback: generate_text() → LMStudio Direct")
    print(f"       → Antwort zurück an TUI")

    # 4. Prompt (gekürzt)
    print(f"\n  [4] Prompt an LLM ({len(result.prompt_text)} chars)")
    for line in result.prompt_text.split("\n")[:10]:
        print(f"       {line}")
    if result.prompt_text.count("\n") > 10:
        print(f"       ... ({result.prompt_text.count('\n') - 10} more lines)")

    # 5. Tatsächliche LMStudio-Antwort
    reply = _probe_lmstudio_chat(
        api_base,
        model,
    )
    print(f"\n  [5] LMStudio Probe-Response")
    print(f"       '{reply}'")

    # 6. TUI Cast
    print(f"\n  [6] TUI .cast Recording")
    print(f"       Scene: snake-mode-live-e2e")
    print(f"       Format: asciinema v2 (newline-delimited JSON)")
    print(f"       Enthält: {result.included_sections}")
    print(f"       Frage + Antwort + Flow-Marker")
    print(f"\n  {"=" * 70}")
    print(f"  FAZIT: Der propose flow sammelt aktiv_target, rolling_summary,")
    print(f"  recent_turns, codecompass, rag und runtime_status als Kontext.")
    print(f"  Diese Informationen werden via /snake/ask an den Hub gesendet,")
    print(f"  der sie entweder an einen Worker oder direkt an LMStudio")
    print(f"  weiterleitet. Die Antwort wird in der TUI angezeigt und")
    print(f"  als .cast aufgezeichnet.")
    print(f"  {"=" * 70}")

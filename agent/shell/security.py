from __future__ import annotations

import logging
import os
import re

try:
    from flask import current_app, has_app_context
except (ImportError, ModuleNotFoundError):
    current_app = None

    def has_app_context():
        return False

from .runtime import settings


def load_blacklist(known_mtime: float) -> tuple[list[str], float]:
    possible_paths = [
        os.path.join(os.getcwd(), "blacklist.txt"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "blacklist.txt"),
    ]
    for path in possible_paths:
        if not os.path.exists(path):
            continue
        try:
            mtime = os.path.getmtime(path)
            if mtime <= known_mtime:
                return [], known_mtime
            with open(path, "r", encoding="utf-8") as handle:
                patterns = [line.strip() for line in handle if line.strip() and not line.strip().startswith("#")]
            logging.info(f"Blacklist geladen ({len(patterns)} Eintraege) von {path}")
            return patterns, mtime
        except Exception as exc:
            logging.error(f"Fehler beim Laden der Blacklist von {path}: {exc}")
            break
    return [], known_mtime


def validate_blacklist_patterns(command: str, blacklist: list[str]) -> tuple[bool, str]:
    for pattern in blacklist:
        try:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Command matches blacklisted pattern '{pattern}'"
        except re.error:
            if pattern in command:
                return False, f"Command contains blacklisted pattern '{pattern}'"
            logging.error(f"Ungueltiges Regex-Pattern in Blacklist: {pattern}")
    return True, ""


def validate_tokens(command: str, *, blacklist: list[str], is_powershell: bool) -> tuple[bool, str]:
    sensitive_patterns = [r"\.git/", r"secrets/", r"\.env", r"token\.json"]
    for pattern in sensitive_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            if not any(cmd in command.lower() for cmd in ["ls ", "cat ", "type ", "dir "]):
                return False, f"Schreibzugriff auf sensiblen Pfad blockiert: {pattern}"
    try:
        import shlex

        tokens = []
        if is_powershell:
            current_token = []
            in_double_quote = False
            in_single_quote = False
            index = 0
            while index < len(command):
                char = command[index]
                if char == "`" and index + 1 < len(command):
                    current_token.append(command[index + 1])
                    index += 2
                    continue
                if char == '"' and not in_single_quote:
                    in_double_quote = not in_double_quote
                    current_token.append(char)
                elif char == "'" and not in_double_quote:
                    in_single_quote = not in_single_quote
                    current_token.append(char)
                elif not in_double_quote and not in_single_quote:
                    if char in " \t\n\r;|^&(){}[]":
                        if current_token:
                            tokens.append("".join(current_token))
                            current_token = []
                        if char.strip():
                            tokens.append(char)
                    else:
                        current_token.append(char)
                else:
                    current_token.append(char)
                index += 1
            if current_token:
                tokens.append("".join(current_token))
        else:
            if os.name == "nt":
                tokens = shlex.split(command, posix=False)
            else:
                tokens = shlex.split(command)

        for token in tokens:
            clean_token = token.strip("'\"")
            if not clean_token:
                continue
            for pattern in blacklist:
                try:
                    if re.search(pattern, clean_token, re.IGNORECASE):
                        return False, f"Gefaehrlicher Token erkannt: '{clean_token}' (Match mit '{pattern}')"
                except re.error:
                    if pattern in clean_token:
                        return False, f"Gefaehrlicher Token erkannt: '{clean_token}' (enthaelt '{pattern}')"
        return True, ""
    except Exception as exc:
        return False, f"Befehls-Analyse fehlgeschlagen: {exc}"


def validate_meta_characters(command: str) -> tuple[bool, str]:
    if "`n" in command or "`r" in command:
        return False, "Mehrzeilige Befehle sind aus Sicherheitsgruenden deaktiviert."
    if "`" in command:
        return False, "Backticks (`) sind aus Sicherheitsgruenden deaktiviert."
    if "$(" in command:
        return False, "Command Substitution $() ist aus Sicherheitsgruenden deaktiviert."
    if re.search(r"\$\w+\$", command) or re.search(r"\}\$\{", command):
        return False, "Variablen-Verkettung ($a$b) ist aus Sicherheitsgr\u00fcnden deaktiviert."
    if ";" in command:
        return False, "Semikolons (;) sind als Befehlstrenner deaktiviert."
    if "&&" in command or "||" in command:
        return False, "Befehlskettung (&&/||) ist aus Sicherheitsgruenden deaktiviert."
    if re.search(r"(^|[^>])>([^>]|$)", command) or "<" in command:
        return False, "Input/Output-Redirection ist aus Sicherheitsgruenden deaktiviert."
    if re.search(r"(^|[^&])&([^&]|$)", command):
        return False, "Background-Execution (&) ist aus Sicherheitsgruenden deaktiviert."
    return True, ""


def analyze_command_intent(command: str) -> tuple[bool, str]:
    try:
        import json

        from agent.llm_integration import _call_llm

        prompt = (
            "Analysiere den folgenden Shell-Befehl auf boesartige Absichten oder extreme Gefaehrlichkeit "
            "(z.B. Loeschen des gesamten Systems, Aendern von Admin-Passwoertern, "
            "Exfiltration sensibler Daten):\n\n"
            f"Befehl: {command}\n\n"
            "Antworte NUR in folgendem JSON-Format:\n"
            "{\n"
            '  "safe": true/false,\n'
            '  "reason": "Begruendung hier"\n'
            "}"
        )

        runtime_cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {}
        runtime_urls = (current_app.config.get("PROVIDER_URLS", {}) or {}) if has_app_context() else {}
        provider = str(runtime_cfg.get("default_provider") or settings.default_provider or "")
        model = str(runtime_cfg.get("default_model") or settings.default_model or "")
        urls = {
            "ollama": runtime_urls.get("ollama") or settings.ollama_url,
            "lmstudio": runtime_urls.get("lmstudio") or settings.lmstudio_url,
            "openai": runtime_urls.get("openai") or settings.openai_url,
            "anthropic": runtime_urls.get("anthropic") or settings.anthropic_url,
        }
        api_key = current_app.config.get("OPENAI_API_KEY") if has_app_context() else None
        raw = _call_llm(provider=provider, model=model, prompt=prompt, urls=urls, api_key=api_key or settings.openai_api_key)
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
            data = json.loads(cleaned)
            safe = data.get("safe")
            if isinstance(safe, str):
                safe = safe.lower() == "true"
            elif safe is None:
                safe = True
            return safe, data.get("reason", "Keine Begruendung angegeben")
        except Exception as exc:
            logging.error(f"Fehler beim Parsen der LLM-Analyse: {exc}. Raw: {raw}")
            if getattr(settings, "fail_secure_llm_analysis", False):
                return False, f"Analyse fehlgeschlagen (Parser-Fehler), Fail-Secure aktiv. Fehler: {exc}"
            return True, "Analyse fehlgeschlagen, Regex-Pruefung war okay."
    except Exception as exc:
        logging.error(f"Fehler bei der Advanced Command Analysis: {exc}")
        if getattr(settings, "fail_secure_llm_analysis", False):
            return False, f"Analyse fehlgeschlagen (LLM-Aufruf), Fail-Secure aktiv. Fehler: {exc}"
        return True, "Analyse-Fehler"

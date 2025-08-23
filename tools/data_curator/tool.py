import argparse
import json
import os
import sys
import shutil
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Iterable, Tuple

# Local imports (same package)
try:
    from .utils import (
        ensure_dir,
        read_text,
        write_text,
        write_jsonl,
        read_jsonl,
        sha256_bytes,
        list_files_recursive,
        stable_random,
        setup_logger,
    )
    from .policy import load_policy
    from .spdx import is_whitelisted_license, SPDX_WHITELIST
    from .utils_ast import ast_summary
except Exception:
    # Allow running as a script directly
    from utils import (
        ensure_dir,
        read_text,
        write_text,
        write_jsonl,
        read_jsonl,
        sha256_bytes,
        list_files_recursive,
        stable_random,
        setup_logger,
    )
    from policy import load_policy
    from spdx import is_whitelisted_license, SPDX_WHITELIST
    try:
        from utils_ast import ast_summary
    except Exception:
        def ast_summary(code: str, lang_hint: str):
            return None

VERSION = "0.1.0"


def cmd_init(args: argparse.Namespace) -> None:
    """Create template sources.yaml and policy.yaml."""
    out_dir = os.path.abspath(args.out or ".")
    ensure_dir(out_dir)
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")
    for name in ("policy.yaml", "license_policy.yaml", "sources.yaml"):
        src = os.path.join(templates_dir, name)
        dst = os.path.join(out_dir, name)
        if not os.path.exists(dst):
            shutil.copyfile(src, dst)
    print(f"Initialized templates in {out_dir}")


def _load_sources(path: str) -> Dict[str, Any]:
    text = read_text(path)
    # Try JSON first for robustness, otherwise very simple YAML parser
    try:
        return json.loads(text)
    except Exception:
        return _simple_yaml_load(text)


def _simple_yaml_load(text: str) -> Dict[str, Any]:
    """Very small YAML subset loader supporting dicts, lists, scalars.
    Assumes 2-space indents, keys without quotes, simple string/number/bool/null values.
    """
    result: Dict[str, Any] = {}
    stack: List[Tuple[int, Any]] = [(-1, result)]
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]
        if line.lstrip().startswith("- "):
            # list item
            val = line.lstrip()[2:].strip()
            if not isinstance(container, list):
                # create a new list in parent by last key if dict
                raise ValueError("YAML structure not supported: list without key")
            container.append(_yaml_scalar(val))
        elif ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == "":
                # start new nested dict or list inferred next lines
                new_obj: Any = {}
                if isinstance(container, dict):
                    container[key] = new_obj
                elif isinstance(container, list):
                    new_obj = {}
                    container.append(new_obj)
                stack.append((indent, new_obj))
            elif val == "|" or val == ">":
                # block scalar: accumulate subsequent indented lines
                block_lines: List[str] = []
                block_indent = None
                # We need a cursor through lines; for simplicity, not supported
                raise ValueError("Block scalars not supported in simple YAML loader")
            else:
                if isinstance(container, dict):
                    container[key] = _yaml_scalar(val)
                else:
                    raise ValueError("Unsupported YAML structure")
        else:
            # Could be list start without '-'
            if isinstance(container, dict):
                # Turn last key into list? Not supported
                pass
    return result


def _yaml_scalar(val: str) -> Any:
    if val in ("true", "True"): return True
    if val in ("false", "False"): return False
    if val in ("null", "Null", "~"): return None
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        return val[1:-1]
    # number?
    try:
        if "." in val:
            return float(val)
        return int(val)
    except Exception:
        return val


# SPDX detection helpers
SPDX_HEADER_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9\-\.\+]+)")


def _detect_spdx_in_text(text: str) -> Optional[str]:
    m = SPDX_HEADER_RE.search(text)
    if m:
        return m.group(1)
    return None


def _detect_repo_license(dir_path: str) -> Optional[str]:
    # Look for LICENSE or COPYING files
    for name in ("LICENSE", "LICENSE.txt", "COPYING", "COPYING.txt"):
        p = os.path.join(dir_path, name)
        if os.path.exists(p):
            try:
                txt = read_text(p)
                spdx = _detect_spdx_in_text(txt)
                if spdx:
                    return spdx
                # crude heuristic: match common license names
                for candidate in SPDX_WHITELIST:
                    if candidate.lower() in txt.lower():
                        return candidate
            except Exception:
                pass
    return None

# --- Policy evaluation helpers ---
_WILDCARD_TOKEN = "*"


def _normalize_spdx_id(lic: Optional[str]) -> str:
    if not lic:
        return ""
    s = (lic or "").strip()
    # strip parentheses and operators
    for sep in ["(", ")", "AND", "OR", "+", ",", ";", "/"]:
        s = s.replace(sep, " ")
    parts = [p for p in s.split() if p]
    base = parts[0] if parts else ""
    return base


def _wildcard_match(pattern: str, value: str) -> bool:
    # Simple wildcard where '*' matches any suffix
    if pattern.endswith(_WILDCARD_TOKEN):
        prefix = pattern[:-1]
        return value.startswith(prefix)
    return pattern == value


def _policy_enforcement_settings(policy: Dict[str, Any]) -> Dict[str, Any]:
    enf = (policy or {}).get("enforcement", {}) or {}
    # defaults if missing
    return {
        "on_unknown": enf.get("on_unknown", "reject"),
        "on_conflict": enf.get("on_conflict", "reject"),
        "require_spdx": bool(enf.get("require_spdx", True)),
    }


def _policy_rule_for_type(policy: Dict[str, Any], ftype: str) -> Dict[str, Any]:
    for rule in (policy or {}).get("rules", []) or []:
        cond = (rule or {}).get("if", {}) or {}
        if cond.get("type") == ftype:
            return rule
    return {}


def _policy_eval(policy: Optional[Dict[str, Any]], license_id: Optional[str], ftype: Optional[str]) -> Tuple[bool, str, str]:
    """Return (allowed, reason, category) where category: whitelist|greylist|blacklist|unknown|conflict.
    This function only evaluates membership; conflict handling is external.
    """
    if not policy:
        # Fallback to built-in whitelist
        ok = is_whitelisted_license(license_id or "")
        return (ok, "builtin_spdx_whitelist" if ok else "not_in_builtin_whitelist", "whitelist" if ok else "unknown")

    lic = _normalize_spdx_id(license_id)
    settings = _policy_enforcement_settings(policy)
    if not lic:
        if settings["on_unknown"] == "reject":
            return (False, "unknown_or_missing_license", "unknown")
        return (True, "unknown_allowed", "unknown")

    # Blacklist immediate deny (with wildcard support)
    for blk in (policy.get("license_blacklist") or []):
        pat = str(blk)
        if _wildcard_match(pat, lic):
            return (False, f"blacklisted:{pat}", "blacklist")

    # Whitelist allow
    for wl in (policy.get("license_whitelist") or []):
        if _wildcard_match(str(wl), lic):
            # type-specific rule may still narrow
            rule = _policy_rule_for_type(policy, (ftype or "").lower())
            if rule.get("allow_only"):
                allowed_set = set(rule.get("allow_only") or [])
                if lic in allowed_set:
                    return (True, "whitelist+type_allow", "whitelist")
                return (False, "type_rule_disallow", "unknown")
            if rule.get("deny"):
                for pat in rule.get("deny") or []:
                    if _wildcard_match(str(pat), lic):
                        return (False, "type_rule_deny", "unknown")
            return (True, "whitelist", "whitelist")

    # Greylist handling: default require manual review -> reject
    for g in (policy.get("license_greylist") or []):
        gid = str((g or {}).get("id", ""))
        if gid and _wildcard_match(gid, lic):
            if (g or {}).get("manual_review", True):
                return (False, "greylist_requires_manual_review", "greylist")
            # if no manual review required and type scope matches, allow
            scope = (g or {}).get("scope")
            if scope == "content_only" and (ftype or "").lower() == "code":
                return (False, "greylist_scope_content_only", "greylist")
            return (True, "greylist_allowed", "greylist")

    # Not in any list
    if settings["on_unknown"] == "reject":
        return (False, "not_in_whitelist_or_greylist", "unknown")
    return (True, "unknown_allowed", "unknown")


def cmd_crawl(args: argparse.Namespace) -> None:
    logger = setup_logger()
    sources = _load_sources(args.sources)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    prov_path = os.path.join(out_dir, "provenance.jsonl")
    audit_path = os.path.join(out_dir, "license_audit.jsonl")
    policy = load_policy(args.policy) if getattr(args, "policy", None) else None
    audit_fields = ((policy or {}).get("audit", {}) or {}).get("record", []) or []

    def write_audit(entry: Dict[str, Any]):
        try:
            with open(audit_path, 'a', encoding='utf-8') as af:
                af.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    rows: List[Dict[str, Any]] = []

    # License gate pre-fetch based on declared/repo license in sources (policy if provided)
    for src in sources.get("sources", []):
        s_type = src.get("type")
        declared = src.get("declared_license")
        if s_type == "local":
            root = os.path.abspath(src["path"])
            repo_spdx = _detect_repo_license(root) or declared
            # Evaluate policy without type-specific rules at prefetch
            allowed, reason, category = _policy_eval(policy, repo_spdx or declared, None)
            if not allowed:
                logger.write(f"DROP pre-fetch (license policy): {src.get('url') or src.get('path')} -> {repo_spdx or declared} ({reason})\n")
                # minimal audit record
                if audit_fields:
                    ent = {
                        "license_spdx": repo_spdx or declared or "",
                        "source_url": src.get("url") or src.get("path") or "",
                        "commit": src.get("commit") or "",
                        "swhid": src.get("swhid") or "",
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "status": "dropped",
                        "stage": "pre_fetch",
                        "reason": reason,
                    }
                    write_audit({k: ent.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
                continue
            src_url = src.get("url", f"file://{root}")
            commit = src.get("commit") or ""
            swhid = src.get("swhid") or None
            files = sorted(list_files_recursive(root))
            for f in files:
                try:
                    rel = os.path.relpath(f, root)
                    b = open(f, 'rb').read()
                    h = sha256_bytes(b)
                    out_file = os.path.join(out_dir, rel)
                    ensure_dir(os.path.dirname(out_file))
                    with open(out_file, 'wb') as wf:
                        wf.write(b)
                    fetched_at = datetime.now(timezone.utc).isoformat()
                    row = {
                        "source_url": src_url,
                        "path": rel.replace("\\", "/"),
                        "commit": commit,
                        "sha256": h,
                        "swhid": swhid,
                        "declared_license": declared,
                        "repo_spdx": repo_spdx,
                        "fetched_at": fetched_at,
                    }
                    rows.append(row)
                    if audit_fields:
                        ent = {
                            "license_spdx": repo_spdx or declared or "",
                            "source_url": src_url,
                            "sha256": h,
                            "commit": commit,
                            "swhid": swhid or "",
                            "fetched_at": fetched_at,
                            "status": "kept",
                            "stage": "pre_fetch",
                            "reason": "pre_fetch_pass",
                        }
                        write_audit({k: ent.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
                except Exception as e:
                    logger.write(f"ERROR copying {f}: {e}\n")
        else:
            logger.write(f"WARN unsupported source type: {s_type}\n")
    write_jsonl(prov_path, rows)
    print(f"Crawl complete. Files copied to {out_dir}. Provenance: {prov_path}")


def cmd_license_scan(args: argparse.Namespace) -> None:
    logger = setup_logger()
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    prov_in = os.path.join(in_dir, "provenance.jsonl")
    prov_rows = read_jsonl(prov_in) if os.path.exists(prov_in) else []
    policy = load_policy(args.policy) if getattr(args, "policy", None) else None
    audit_fields = ((policy or {}).get("audit", {}) or {}).get("record", []) or []
    audit_path = os.path.join(out_dir, "license_audit.jsonl")

    def write_audit(entry: Dict[str, Any]):
        if not audit_fields:
            return
        try:
            with open(audit_path, 'a', encoding='utf-8') as af:
                af.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass

    histogram: Dict[str, int] = {}
    kept = 0
    dropped = 0
    for f in sorted(list_files_recursive(in_dir)):
        basename = os.path.basename(f)
        if basename in ("provenance.jsonl", "license_histogram.json", "license_audit.jsonl"):
            continue
        rel = os.path.relpath(f, in_dir)
        # Infer file type for rules
        ext = os.path.splitext(rel)[1].lower()
        ftype = "code" if ext in CODE_EXTS else "content"
        try:
            txt = read_text(f)
        except Exception:
            # Binary or unreadable → drop
            dropped += 1
            logger.write(f"DROP unreadable/binary: {rel}\n")
            # audit
            prov = next((p for p in prov_rows if p.get("path") == rel.replace("\\", "/")), None)
            ent_base = {
                "license_spdx": "",
                "source_url": (prov or {}).get("source_url", ""),
                "sha256": (prov or {}).get("sha256", ""),
                "commit": (prov or {}).get("commit", ""),
                "swhid": (prov or {}).get("swhid", ""),
                "fetched_at": (prov or {}).get("fetched_at", ""),
                "status": "dropped",
                "stage": "post_fetch",
                "reason": "unreadable_or_binary",
            }
            write_audit({k: ent_base.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
            continue
        header_spdx = _detect_spdx_in_text(txt)
        # repo/declaration from provenance if available
        prov = next((p for p in prov_rows if p.get("path") == rel.replace("\\", "/")), None)
        repo_spdx = (prov or {}).get("repo_spdx") or _detect_repo_license(in_dir)
        declared = (prov or {}).get("declared_license")

        # Conflict detection
        norms = set(x for x in [
            _normalize_spdx_id(header_spdx),
            _normalize_spdx_id(repo_spdx),
            _normalize_spdx_id(declared),
        ] if x)
        settings = _policy_enforcement_settings(policy or {})
        if len(norms) > 1 and settings.get("on_conflict", "reject") == "reject":
            dropped += 1
            logger.write(f"DROP post-fetch (conflict): {rel} -> header={header_spdx}, repo={repo_spdx}, declared={declared}\n")
            ent_conf = {
                "license_spdx": header_spdx or repo_spdx or declared or "",
                "source_url": (prov or {}).get("source_url", ""),
                "sha256": (prov or {}).get("sha256", ""),
                "commit": (prov or {}).get("commit", ""),
                "swhid": (prov or {}).get("swhid", ""),
                "fetched_at": (prov or {}).get("fetched_at", ""),
                "status": "dropped",
                "stage": "post_fetch",
                "reason": "license_conflict",
            }
            write_audit({k: ent_conf.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
            continue

        # Choose effective license (prefer header, then repo, then declared)
        effective = header_spdx or repo_spdx or declared or ""
        allowed, reason, category = _policy_eval(policy, effective, ftype)
        if not allowed:
            dropped += 1
            logger.write(f"DROP post-fetch (license policy): {rel} -> {effective} ({reason})\n")
            ent_drop = {
                "license_spdx": effective,
                "source_url": (prov or {}).get("source_url", ""),
                "sha256": (prov or {}).get("sha256", ""),
                "commit": (prov or {}).get("commit", ""),
                "swhid": (prov or {}).get("swhid", ""),
                "fetched_at": (prov or {}).get("fetched_at", ""),
                "status": "dropped",
                "stage": "post_fetch",
                "reason": reason,
            }
            write_audit({k: ent_drop.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
            continue

        # keep
        norm_eff = _normalize_spdx_id(effective) or effective
        histogram[norm_eff] = histogram.get(norm_eff, 0) + 1
        out_file = os.path.join(out_dir, rel)
        ensure_dir(os.path.dirname(out_file))
        shutil.copyfile(f, out_file)
        kept += 1
        ent_keep = {
            "license_spdx": norm_eff,
            "source_url": (prov or {}).get("source_url", ""),
            "sha256": (prov or {}).get("sha256", ""),
            "commit": (prov or {}).get("commit", ""),
            "swhid": (prov or {}).get("swhid", ""),
            "fetched_at": (prov or {}).get("fetched_at", ""),
            "status": "kept",
            "stage": "post_fetch",
            "reason": "post_fetch_pass",
        }
        write_audit({k: ent_keep.get(k, "") for k in set(audit_fields) | {"status","stage","reason"}})
    # write histogram and provenance passthrough
    write_text(os.path.join(out_dir, "license_histogram.json"), json.dumps(histogram, indent=2))
    if prov_rows:
        write_jsonl(os.path.join(out_dir, "provenance.jsonl"), prov_rows)
    print(f"License scan complete. Kept={kept}, Dropped={dropped}")


# Simple parsers
MD_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _parse_markdown(text: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    # extract code blocks
    for m in MD_CODE_BLOCK_RE.finditer(text):
        lang = m.group(1) or ""
        code = m.group(2)
        blocks.append({"type": "code", "lang": lang, "text": code})
    # tables
    lines = text.splitlines()
    table_buf: List[str] = []
    for ln in lines:
        if TABLE_LINE_RE.match(ln):
            table_buf.append(ln)
        else:
            if table_buf:
                blocks.append({"type": "table", "text": "\n".join(table_buf)})
                table_buf = []
    if table_buf:
        blocks.append({"type": "table", "text": "\n".join(table_buf)})
    # remaining text (naive)
    blocks.append({"type": "text", "text": text})
    return blocks


def _parse_plain(text: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": text}]


CODE_EXTS = {".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".cs"}
MD_EXTS = {".md", ".markdown", ".rst"}


def cmd_parse(args: argparse.Namespace) -> None:
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    out_jsonl = os.path.join(out_dir, "parsed.jsonl")
    prov_rows = read_jsonl(os.path.join(in_dir, "provenance.jsonl")) if os.path.exists(os.path.join(in_dir, "provenance.jsonl")) else []
    prov_by_path = {p.get("path"): p for p in prov_rows}

    records: List[Dict[str, Any]] = []
    for f in sorted(list_files_recursive(in_dir)):
        if os.path.basename(f) in ("provenance.jsonl", "license_histogram.json"):
            continue
        rel = os.path.relpath(f, in_dir).replace("\\", "/")
        try:
            txt = read_text(f)
        except Exception:
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in MD_EXTS:
            blocks = _parse_markdown(txt)
            ftype = "markdown" if ext != ".rst" else "rest"
        elif ext in CODE_EXTS:
            # naive code block: whole file as code
            lang_hint = ext.lstrip(".")
            blocks = [{"type": "code", "lang": lang_hint, "text": txt}]
            ftype = "code"
        else:
            blocks = _parse_plain(txt)
            ftype = "text"
        rec = {
            "path": rel,
            "filetype": ftype,
            "blocks": blocks,
            "provenance": prov_by_path.get(rel, {}),
        }
        if ftype == "code":
            try:
                summary = ast_summary(txt, lang_hint)
            except Exception:
                summary = None
            if summary:
                rec["ast"] = summary
        records.append(rec)
    write_jsonl(out_jsonl, records)
    print(f"Parsed {len(records)} files -> {out_jsonl}")


# Dedup utilities
WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def simhash(text: str, seed: int = 42, bits: int = 64) -> int:
    words = WORD_RE.findall(text.lower())
    rnd = stable_random(seed)
    # deterministic hash bucket for words
    def wh(w: str) -> int:
        # Use sha1 for stability, then fold to bits
        h = hashlib.sha1(w.encode("utf-8")).digest()
        val = int.from_bytes(h[:8], "big")
        return val
    v = [0] * bits
    for w in words:
        hv = wh(w)
        for i in range(bits):
            if hv >> i & 1:
                v[i] += 1
            else:
                v[i] -= 1
    out = 0
    for i in range(bits):
        if v[i] >= 0:
            out |= (1 << i)
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def cmd_dedup(args: argparse.Namespace) -> None:
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    in_jsonl = os.path.join(in_dir, "parsed.jsonl")
    records = read_jsonl(in_jsonl)

    exact_seen: set = set()
    near_seen: List[int] = []
    near_threshold = int(args.near_threshold)

    unique: List[Dict[str, Any]] = []
    for rec in records:
        blocks = rec.get("blocks", [])
        new_blocks = []
        for b in blocks:
            text = b.get("text", "")
            h_exact = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if h_exact in exact_seen:
                continue
            exact_seen.add(h_exact)
            sh = simhash(text, seed=int(args.seed))
            if any(hamming(sh, prev) <= near_threshold for prev in near_seen):
                continue
            near_seen.append(sh)
            new_blocks.append(b)
        if new_blocks:
            rec2 = dict(rec)
            rec2["blocks"] = new_blocks
            unique.append(rec2)
    out_jsonl = os.path.join(out_dir, "parsed.unique.jsonl")
    write_jsonl(out_jsonl, unique)
    print(f"Dedup complete. {len(unique)} files with unique blocks -> {out_jsonl}")


# Chunking with simple tokenization and PII filtering
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b\+?[0-9][0-9\-\s]{6,}[0-9]\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def contains_pii(text: str, extra_block_patterns: List[str]) -> bool:
    if EMAIL_RE.search(text) or PHONE_RE.search(text) or IP_RE.search(text):
        return True
    for pat in extra_block_patterns:
        try:
            if re.search(pat, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def tokenize(text: str) -> List[str]:
    return WORD_RE.findall(text)


def semantic_split(blocks: List[Dict[str, Any]]) -> List[str]:
    # Respect block boundaries; simply return block texts
    return [b.get("text", "") for b in blocks if b.get("text")]


def chunk_texts(texts: List[str], min_tokens: int, max_tokens: int) -> List[str]:
    chunks: List[str] = []
    buf: List[str] = []
    count = 0
    for t in texts:
        toks = tokenize(t)
        if count + len(toks) > max_tokens and buf:
            chunks.append("\n\n".join(buf))
            buf, count = [], 0
        buf.append(t)
        count += len(toks)
        if count >= min_tokens:
            chunks.append("\n\n".join(buf))
            buf, count = [], 0
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def cmd_chunk(args: argparse.Namespace) -> None:
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    policy = load_policy(args.policy) if args.policy else {"block_patterns": []}
    block_patterns = policy.get("block_patterns", []) or []

    records = read_jsonl(os.path.join(in_dir, "parsed.unique.jsonl"))
    out_rows: List[Dict[str, Any]] = []
    dropped_pii = 0
    for rec in records:
        texts = semantic_split(rec.get("blocks", []))
        chunks = chunk_texts(texts, int(args.min_tokens), int(args.max_tokens))
        for ch in chunks:
            if contains_pii(ch, block_patterns):
                dropped_pii += 1
                continue
            out_rows.append({
                "text": ch,
                "path": rec.get("path"),
                "filetype": rec.get("filetype"),
                "provenance": rec.get("provenance", {}),
            })
    out_jsonl = os.path.join(out_dir, "chunks.jsonl")
    write_jsonl(out_jsonl, out_rows)
    print(f"Chunked {len(out_rows)} chunks written. Dropped for PII: {dropped_pii}")


# Tagging heuristics
EXT_LANG_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".cpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
}


def infer_language_by_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return EXT_LANG_MAP.get(ext, "Plaintext")


def infer_domain_label(source_url: str) -> str:
    if not source_url:
        return "unknown"
    s = source_url.lower()
    if "github" in s or "gitlab" in s or "bitbucket" in s:
        return "code-hosting"
    if s.startswith("file://"):
        return "local"
    if "docs" in s or "readthedocs" in s:
        return "docs"
    return "web"


def cmd_tag(args: argparse.Namespace) -> None:
    in_dir = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    rows = read_jsonl(os.path.join(in_dir, "chunks.jsonl"))
    tagged: List[Dict[str, Any]] = []
    for r in rows:
        prov = r.get("provenance", {})
        lang = infer_language_by_path(r.get("path") or "")
        domain = infer_domain_label(prov.get("source_url", ""))
        r2 = dict(r)
        r2["tags"] = {"language": lang, "domain": domain, "filetype": r.get("filetype")}
        tagged.append(r2)
    out_jsonl = os.path.join(out_dir, "tagged.jsonl")
    write_jsonl(out_jsonl, tagged)
    print(f"Tagging complete: {len(tagged)} records -> {out_jsonl}")


def cmd_export(args: argparse.Namespace) -> None:
    in_dir = os.path.abspath(args.input)
    out_path = os.path.abspath(args.out)
    manifest_path = os.path.abspath(args.manifest)
    ensure_dir(os.path.dirname(out_path))
    ensure_dir(os.path.dirname(manifest_path))
    rows = read_jsonl(os.path.join(in_dir, "tagged.jsonl"))
    # dataset jsonl
    write_jsonl(out_path, rows)

    # try load license histogram from current or parent dirs up to 3 levels
    def find_license_histogram(start_dir: str) -> Dict[str, int]:
        cur = start_dir
        for _ in range(4):
            p = os.path.join(cur, "license_histogram.json")
            if os.path.exists(p):
                try:
                    return json.loads(read_text(p))
                except Exception:
                    return {}
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return {}

    license_hist = find_license_histogram(in_dir)

    manifest = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": int(args.seed),
        "counts": {
            "records": len(rows),
        },
        "sources": args.sources or "",
        "license_histogram": license_hist,
        "hashes": {
            "dataset_sha256": sha256_bytes("\n".join(json.dumps(r, sort_keys=True) for r in rows).encode("utf-8")),
        },
    }
    write_text(manifest_path, json.dumps(manifest, indent=2))

    # Optional Parquet export
    if getattr(args, "parquet", None):
        try:
            import pyarrow as pa  # type: ignore
            import pyarrow.parquet as pq  # type: ignore
            def flatten(r: Dict[str, Any]) -> Dict[str, Any]:
                tags = r.get("tags", {}) or {}
                prov = r.get("provenance", {}) or {}
                return {
                    "text": r.get("text"),
                    "path": r.get("path"),
                    "filetype": r.get("filetype"),
                    "tag_language": tags.get("language"),
                    "tag_domain": tags.get("domain"),
                    "tag_filetype": tags.get("filetype"),
                    "source_url": prov.get("source_url"),
                    "commit": prov.get("commit"),
                    "sha256": prov.get("sha256"),
                    "swhid": prov.get("swhid"),
                    "repo_spdx": prov.get("repo_spdx"),
                    "declared_license": prov.get("declared_license"),
                }
            flat_rows = [flatten(r) for r in rows]
            table = pa.Table.from_pylist(flat_rows)
            pq.write_table(table, os.path.abspath(args.parquet))
        except Exception as e:
            print(f"Parquet export skipped: {e}")

    print(f"Exported dataset -> {out_path}; manifest -> {manifest_path}")


def cmd_report(args: argparse.Namespace) -> None:
    manifest_path = os.path.abspath(args.manifest)
    out_dir = os.path.abspath(args.out)
    ensure_dir(out_dir)
    man = json.loads(read_text(manifest_path))
    html = [
        "<html><head><meta charset='utf-8'><title>Curation Report</title></head><body>",
        f"<h1>Data Curation Report v{VERSION}</h1>",
        f"<p>Created: {man.get('created_at')}</p>",
        f"<h2>Counts</h2><pre>{json.dumps(man.get('counts', {}), indent=2)}</pre>",
        f"<h2>License Histogram</h2><pre>{json.dumps(man.get('license_histogram', {}), indent=2)}</pre>",
        f"<h2>Hashes</h2><pre>{json.dumps(man.get('hashes', {}), indent=2)}</pre>",
        "</body></html>",
    ]
    write_text(os.path.join(out_dir, "curation.html"), "\n".join(html))
    print(f"Report written to {os.path.join(out_dir, 'curation.html')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tool", description="Data Acquisition & Curation Tool (SPDX-whitelist)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="Create templates for sources and policy")
    sp.add_argument("--out", default=".")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("crawl", help="Fetch/copy sources to raw/")
    sp.add_argument("--sources", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--policy", help="Path to license policy YAML/JSON", required=False)
    sp.set_defaults(func=cmd_crawl)

    sp = sub.add_parser("license-scan", help="Scan and filter by license policy/SPDX whitelist")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--policy", help="Path to license policy YAML/JSON", required=False)
    sp.set_defaults(func=cmd_license_scan)

    sp = sub.add_parser("parse", help="Parse files into typed blocks")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("dedup", help="Remove exact and near duplicates")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--near-threshold", default="3")
    sp.add_argument("--seed", default="42")
    sp.set_defaults(func=cmd_dedup)

    sp = sub.add_parser("chunk", help="Chunk parsed data deterministically")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--policy")
    sp.add_argument("--min-tokens", dest="min_tokens", default="512")
    sp.add_argument("--max-tokens", dest="max_tokens", default="2048")
    sp.set_defaults(func=cmd_chunk)

    sp = sub.add_parser("tag", help="Add language/filetype/domain tags")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_tag)

    sp = sub.add_parser("export", help="Export JSONL and manifest")
    sp.add_argument("--in", dest="input", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--manifest", required=True)
    sp.add_argument("--sources")
    sp.add_argument("--seed", default="42")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("report", help="Generate curation HTML report")
    sp.add_argument("--manifest", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_report)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    try:
        args.func(args)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

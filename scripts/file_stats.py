#!/usr/bin/env python3
"""Recursive file stats by extension: tokens, chars, DIN A4 pages."""

import argparse
import json
import os
import sys
import pathspec
from collections import defaultdict

TOKEN_MODELS = {
    "gpt-4":     4.0,   # OpenAI GPT-4 (cl100k_base)
    "gpt-4o":    4.2,
    "claude":    3.7,   # Anthropic Claude (denser tokenizer)
    "llama":     4.5,   # Llama 2/3 SentencePiece
    "gemma":     4.5,   # Gemma 2B/7B (SentencePiece, wie Llama)
    "phi":       4.0,   # Phi-3/3.5/4 (cl100k_base, wie GPT-4)
    "mistral":   4.5,   # Mistral / Mixtral (SentencePiece)
    "qwen":      4.0,   # Qwen2 (eigener Tokenizer)
    "deepseek":  4.3,
    "gemini":    4.0,
    "code":      3.5,   # Code-heavy (mehr Tokens pro Char)
}

CHARS_PER_A4_PAGE = 1500.0

ALWAYS_IGNORE = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", ".eggs", "eggs", "dist", "build", ".next", ".nuxt",
    ".cache", ".sass-cache", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".hypothesis", ".coverage", "coverage",
    ".svn", ".hg", "target", "bin", "obj", ".idea", ".vscode",
}


def load_gitignore_patterns(root):
    specs = [pathspec.PathSpec.from_lines("gitwildmatch", [])]
    gitignore_path = os.path.join(root, ".gitignore")
    if os.path.isfile(gitignore_path):
        with open(gitignore_path) as f:
            lines = [l.split("#")[0].strip() for l in f
                     if l.strip() and not l.strip().startswith("#")]
            if lines:
                specs.append(pathspec.PathSpec.from_lines("gitwildmatch", lines))
    return specs


def is_ignored(rel_path, specs, no_ignore):
    if not no_ignore:
        for s in specs:
            if s.match_file(rel_path):
                return True
    parts = rel_path.split(os.sep)
    for p in parts:
        if p in ALWAYS_IGNORE:
            return True
    return False


def walk(root, specs, no_ignore):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(os.path.join(rel_dir, d) if rel_dir else d, specs, no_ignore)
        ]
        dirnames.sort()
        for f in sorted(filenames):
            rel_path = os.path.join(rel_dir, f) if rel_dir else f
            if is_ignored(rel_path, specs, no_ignore):
                continue
            yield os.path.join(dirpath, f), rel_path


def format_size(n):
    for unit in ("", "K", "M", "G"):
        if abs(n) < 1024:
            return f"{n:>8.1f}{unit}"
        n /= 1024
    return f"{n:>8.1f}T"


def main():
    parser = argparse.ArgumentParser(
        description="Recursive file statistics by extension: chars, tokens, DIN A4 pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                                   # current dir, GPT-4\n"
            "  %(prog)s /path/to/project -m claude         # Claude tokenizer\n"
            "  %(prog)s -m code -t 10                      # top 10, code ratio\n"
            "  %(prog)s --no-ignore                    # full scan (ohne .gitignore)\n"
            "  %(prog)s -o stats.json                      # JSON export\n"
            "  %(prog)s -v                                 # per-file details\n"
        ),
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Root directory to scan (default: .)")
    parser.add_argument("-m", "--token-model",
                        choices=list(TOKEN_MODELS) + ["custom"],
                        default="gpt-4",
                        help="Tokenization model preset (default: gpt-4)")
    parser.add_argument("-c", "--chars-per-token", type=float,
                        help="Custom chars/token ratio (overrides --token-model)")
    parser.add_argument("-p", "--chars-per-page", type=float,
                        default=CHARS_PER_A4_PAGE,
                        help=f"Chars per DIN A4 page (default: {CHARS_PER_A4_PAGE:.0f})")
    parser.add_argument("-n", "--min-files", type=int, default=1,
                        help="Minimum file count to show an extension (default: 1)")
    parser.add_argument("-t", "--top", type=int,
                        help="Show only top N extensions")
    parser.add_argument("-o", "--output",
                        help="Export result as JSON to FILE")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show per-file details")
    parser.add_argument("--no-ignore", action="store_true",
                        help="Do not respect .gitignore")
    args = parser.parse_args()

    chars_per_token = args.chars_per_token or TOKEN_MODELS[args.token_model]
    chars_per_page = args.chars_per_page
    tokens_per_page = chars_per_page / chars_per_token

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"Fehler: '{root}' ist kein Verzeichnis.", file=sys.stderr)
        sys.exit(1)

    specs = load_gitignore_patterns(root)
    ext_stats = defaultdict(lambda: {"files": 0, "chars": 0, "tokens": 0})
    total = {"files": 0, "chars": 0, "tokens": 0}
    per_file = []

    for abspath, rel_path in walk(root, specs, args.no_ignore):
        try:
            chars = os.path.getsize(abspath)
        except OSError:
            continue
        ext = os.path.splitext(rel_path)[1].lower() or "(no ext)"
        tokens = chars / chars_per_token
        ext_stats[ext]["files"] += 1
        ext_stats[ext]["chars"] += chars
        ext_stats[ext]["tokens"] += tokens
        total["files"] += 1
        total["chars"] += chars
        total["tokens"] += tokens
        if args.verbose:
            per_file.append((rel_path, ext, chars, tokens))

    if not ext_stats:
        print("Keine Dateien gefunden.")
        return

    # Filter + sort
    sorted_exts = sorted(ext_stats.items(), key=lambda x: x[1]["tokens"], reverse=True)
    sorted_exts = [(e, s) for e, s in sorted_exts if s["files"] >= args.min_files]
    if args.top:
        sorted_exts = sorted_exts[:args.top]

    print(f"\nModell: {args.token_model} ({chars_per_token} Zeichen/Token)")
    print(f"{'Extension':<18} {'Files':>8} {'Chars':>12} {'Size':>10} {'Tokens':>10} {'A4 Pages':>10}")
    print("-" * 70)
    for ext, s in sorted_exts:
        a4 = s["tokens"] / tokens_per_page
        size_h = format_size(s["chars"])
        print(f"{ext:<18} {s['files']:>8} {s['chars']:>12,} {size_h:>10} {s['tokens']:>10,.0f} {a4:>10.1f}")
    print("-" * 70)
    a4_total = total["tokens"] / tokens_per_page
    size_h = format_size(total["chars"])
    print(f"{'Total':<18} {total['files']:>8} {total['chars']:>12,} {size_h:>10} {total['tokens']:>10,.0f} {a4_total:>10.1f}")

    if args.verbose:
        print(f"\n--- Per-File Details ({len(per_file)} Dateien) ---")
        print(f"{'Datei':<60} {'Ext':<10} {'Chars':>10} {'Tokens':>10}")
        print("-" * 92)
        for path, ext, chars, tokens in per_file:
            print(f"{path:<60} {ext:<10} {chars:>10,} {tokens:>10,.0f}")

    if args.output:
        data = {
            "modell": args.token_model,
            "chars_per_token": chars_per_token,
            "chars_per_page": chars_per_page,
            "extensions": {e: s for e, s in sorted_exts},
            "total": dict(total),
        }
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nJSON exportiert nach: {args.output}")


if __name__ == "__main__":
    main()

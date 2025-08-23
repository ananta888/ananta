from typing import Optional, Dict, Any

# Optional AST summary via tree_sitter_languages if available
try:
    from tree_sitter_languages import get_language, get_parser  # type: ignore
except Exception:  # pragma: no cover
    get_language = None  # type: ignore
    get_parser = None  # type: ignore

_LANG_MAP = {
    'py': 'python',
    'python': 'python',
    'js': 'javascript',
    'ts': 'typescript',
    'java': 'java',
    'go': 'go',
    'rs': 'rust',
    'c': 'c',
    'cpp': 'cpp',
    'rb': 'ruby',
    'php': 'php',
    'cs': 'c_sharp',
}


def ast_summary(code: str, lang_hint: str) -> Optional[Dict[str, Any]]:
    if get_parser is None:
        return None
    lang_key = _LANG_MAP.get(lang_hint.lower()) or lang_hint.lower()
    try:
        parser = get_parser(lang_key)
    except Exception:
        return None
    try:
        tree = parser.parse(code.encode('utf-8'))
        # Simple summary: total nodes by traversing
        cursor = tree.walk()
        count = 1
        def walk(c):
            nonlocal count
            if c.goto_first_child():
                count += 1
                walk(c)
                while c.goto_next_sibling():
                    count += 1
                    walk(c)
                c.goto_parent()
        walk(cursor)
        return {"ast_nodes": count, "lang": lang_key}
    except Exception:
        return None

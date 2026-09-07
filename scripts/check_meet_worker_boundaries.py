"""Parse the standalone Meet Worker package; never import/execute audited code."""

import argparse
import ast
from pathlib import Path

HUB_TASK_CALLS = frozenset({"get_task_queue_service", "ingest_task", "start_dialog"})


def _hub_module(name):
    return name == "agent" or name.startswith("agent.")


def source_findings(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return [(1, "meet_worker_source_invalid")]
    findings, aliases = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
                if _hub_module(item.name):
                    findings.add((node.lineno, "meet_worker_hub_import"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not node.level and _hub_module(module):
                findings.add((node.lineno, "meet_worker_hub_import"))
            for item in node.names:
                aliases[item.asname or item.name] = module + "." + item.name

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + "." + node.attr
        return ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = name(node.func)
        if called.rsplit(".", 1)[-1] in HUB_TASK_CALLS:
            findings.add((node.lineno, "meet_worker_task_ownership"))
        if called in {"__import__", "builtins.__import__", "importlib.import_module"} and node.args:
            target = node.args[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str) and _hub_module(target.value):
                findings.add((node.lineno, "meet_worker_hub_import"))
    return sorted(findings)


def audit(root):
    root = Path(root).resolve()
    package = root / "worker/meet_media"
    if not package.is_dir() or package.is_symlink():
        raise ValueError("meet_worker_package_missing_or_linked")
    files = sorted(package.rglob("*.py"))
    if not files or len(files) > 256:
        raise ValueError("meet_worker_source_count_invalid")
    result, total = [], 0
    for file in files:
        if file.is_symlink() or not file.resolve().is_relative_to(package):
            raise ValueError("meet_worker_source_link_invalid")
        with file.open("rb") as source:
            raw = source.read(262145)
        total += len(raw)
        if len(raw) > 262144 or total > 4 * 1024 * 1024:
            raise ValueError("meet_worker_source_size_invalid")
        try:
            findings = source_findings(raw.decode("utf-8"))
        except UnicodeError:
            findings = [(1, "meet_worker_source_invalid")]
        result.extend(
            {"path": file.relative_to(root).as_posix(), "line": line, "code": code} for line, code in findings
        )
    return len(files), result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        count, findings = audit(args.root)
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ValueError) else "meet_worker_source_unreadable")
        return 1
    for finding in findings:
        print(f"{finding['path']}:{finding['line']}: {finding['code']}")
    if not findings:
        print(f"meet-worker-boundaries-ok: {count} files")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())

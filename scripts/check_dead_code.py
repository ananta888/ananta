import os
import ast
import sys
from collections import Counter
from typing import Dict, List, Set, Tuple

def get_definitions_and_references(file_path: str) -> Tuple[Set[str], List[str]]:
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except Exception:
            return set(), []

    definitions = set()
    references = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Check if it is a flask route - they are technically used by the framework
            is_route = False
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and hasattr(decorator.func, 'attr') and decorator.func.attr == 'route':
                    is_route = True
                elif isinstance(decorator, ast.Attribute) and decorator.attr == 'route':
                    is_route = True

            if not is_route:
                definitions.add(node.name)

        if isinstance(node, ast.Name):
            references.append(node.id)
        elif isinstance(node, ast.Attribute):
            references.append(node.attr)

    return definitions, references

def main():
    root_dir = os.path.abspath("agent")
    print(f"Analyzing dead code in {root_dir}...")

    all_definitions: Dict[str, str] = {} # name -> file
    all_references = Counter()

    for root, _, files in os.walk(root_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                defs, refs = get_definitions_and_references(file_path)

                for d in defs:
                    all_definitions[d] = file_path
                all_references.update(refs)

    dead_code = []
    for name, file_path in all_definitions.items():
        # If count is 1, it's only the definition itself
        # Note: This is a simple heuristic and might have false positives
        # (e.g. names that match other common strings)
        if all_references[name] <= 1:
            # Filter out common entry points or special methods
            if name.startswith("__") or name == "main":
                continue
            dead_code.append((name, file_path))

    if dead_code:
        print(f"Found {len(dead_code)} potentially unused definitions:")
        # Sort by file path
        dead_code.sort(key=lambda x: x[1])
        for name, file_path in dead_code:
            rel_path = os.path.relpath(file_path, os.getcwd())
            print(f"- {name} in {rel_path}")

        # We don't exit with error because this is very heuristic
        sys.exit(0)
    else:
        print("No dead code found (heuristic).")
        sys.exit(0)

if __name__ == "__main__":
    main()

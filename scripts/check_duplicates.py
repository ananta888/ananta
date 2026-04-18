import os
import sys
import difflib
from typing import List, Tuple

def get_file_content(file_path: str) -> List[str]:
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f.readlines() if line.strip() and not line.strip().startswith("#")]

def check_duplicates(root_dir: str, threshold: float = 0.85) -> List[Tuple[str, str, float]]:
    py_files = []
    for root, _, files in os.walk(root_dir):
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    duplicates = []
    contents = {f: get_file_content(f) for f in py_files}

    # Only compare files within the same category (services with services, routes with routes)
    # to avoid O(N^2) over the whole project and focus on relevant duplicates
    categories = ["agent/services", "agent/routes", "agent/models", "agent/common"]

    for cat in categories:
        cat_dir = os.path.abspath(cat)
        cat_files = [f for f in py_files if f.startswith(cat_dir)]

        for i in range(len(cat_files)):
            for j in range(i + 1, len(cat_files)):
                f1, f2 = cat_files[i], cat_files[j]
                if not contents[f1] or not contents[f2]:
                    continue

                # Simple heuristic: if length differs significantly, they are probably not duplicates
                len1, len2 = len(contents[f1]), len(contents[f2])
                if min(len1, len2) / max(len1, len2) < threshold:
                    continue

                sm = difflib.SequenceMatcher(None, contents[f1], contents[f2])
                ratio = sm.ratio()
                if ratio > threshold:
                    duplicates.append((f1, f2, ratio))

    return duplicates

def main():
    root_dir = os.path.abspath("agent")
    print(f"Checking for duplicate code in {root_dir}...")

    threshold = 0.85
    duplicates = check_duplicates(root_dir, threshold=threshold)

    if duplicates:
        print(f"Found {len(duplicates)} potential duplicates (similarity > {threshold:.0%}):")
        for f1, f2, ratio in duplicates:
            rel_f1 = os.path.relpath(f1, os.getcwd())
            rel_f2 = os.path.relpath(f2, os.getcwd())
            print(f"- {rel_f1} <-> {rel_f2} ({ratio:.2%})")
        # We don't exit with 1 yet, as these might be false positives
        # But we report them.
        sys.exit(0)
    else:
        print("No significant duplicates found.")
        sys.exit(0)

if __name__ == "__main__":
    main()

import os
import sys
import subprocess
import re
from typing import List, Set

REQUIRED_FILES = [
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "pyproject.toml",
    "alembic.ini",
    "PROJEKT_ZIELBILD.md",
    "CONTRIBUTING.md",
]

def check_files_exist(files: List[str]) -> bool:
    print("\n--- Checking Required Files ---")
    missing = []
    for f in files:
        if not os.path.exists(f):
            missing.append(f)
            print(f"❌ Missing: {f}")
        else:
            print(f"✅ Found: {f}")

    if missing:
        return False
    return True

def get_deps_from_pyproject() -> Set[str]:
    deps = set()
    try:
        with open("pyproject.toml", "r") as f:
            content = f.read()
            # Simple regex to find dependencies in [project] dependencies list
            # We look for strings in quotes within the dependencies = [...] block
            dep_block = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
            if dep_block:
                deps.update(re.findall(r'"([^"]+)"', dep_block.group(1)))
    except Exception as e:
        print(f"Error reading pyproject.toml: {e}")
    return deps

def get_deps_from_requirements() -> Set[str]:
    deps = set()
    try:
        with open("requirements.txt", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Basic extraction, ignoring versions for simplicity in this check
                    dep = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                    deps.add(dep)
    except Exception as e:
        print(f"Error reading requirements.txt: {e}")
    return deps

def check_dependency_consistency() -> bool:
    print("\n--- Checking Dependency Consistency ---")
    pyproject_deps = get_deps_from_pyproject()
    req_deps = get_deps_from_requirements()

    # We only check if core dependencies (non-dev) match
    # dev dependencies in requirements.txt are marked with a comment

    missing_in_req = pyproject_deps - req_deps
    if missing_in_req:
        print(f"❌ Dependencies in pyproject.toml but missing in requirements.txt: {missing_in_req}")
        return False

    print("✅ Dependencies are consistent (at least all from pyproject are in requirements).")
    return True

def check_docker_context() -> bool:
    print("\n--- Checking Docker Context ---")
    # Check if there are large files that shouldn't be in the docker context
    # This is a simple check for common bloat
    bloat_candidates = [".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules"]
    # We check if they exist and if there's a .dockerignore
    if not os.path.exists(".dockerignore"):
        print("⚠️ Warning: .dockerignore is missing. This may lead to bloated images.")
        return True # Not a hard fail, but a warning

    print("✅ .dockerignore exists.")
    return True

def main():
    print("Ananta Release Gate")
    print("===================")

    success = True
    if not check_files_exist(REQUIRED_FILES):
        success = False

    if not check_dependency_consistency():
        success = False

    if not check_docker_context():
        success = False

    if success:
        print("\n✅ Release Gate passed! The repository is ready for a build/release.")
        sys.exit(0)
    else:
        print("\n❌ Release Gate failed. Please fix the issues above.")
        sys.exit(1)

if __name__ == "__main__":
    main()

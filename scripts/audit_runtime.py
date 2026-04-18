import os
import sys
import subprocess
import shutil
from pathlib import Path

def check_binary(name: str):
    path = shutil.which(name)
    if path:
        print(f"✅ {name}: {path}")
        return True
    else:
        print(f"❌ {name}: NOT FOUND")
        return False

def audit_dependencies():
    print("\n--- Dependency Audit ---")
    req_file = Path("requirements.txt")
    if req_file.exists():
        with open(req_file, "r") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        print(f"Requirements count: {len(lines)}")
    else:
        print("❌ requirements.txt missing")

def audit_binaries():
    print("\n--- Binary Audit ---")
    binaries = ["python", "pip", "ruff", "mypy", "pytest", "docker", "docker-compose", "git"]
    for b in binaries:
        check_binary(b)

def audit_python_env():
    print("\n--- Python Environment ---")
    print(f"Python Version: {sys.version}")
    print(f"Platform: {sys.platform}")
    # Pruefe ob wir in einem venv sind
    is_venv = sys.prefix != sys.base_prefix
    print(f"Virtual Env: {'Yes' if is_venv else 'No'} ({sys.prefix})")

def main():
    print("=== Ananta Runtime Audit ===")
    audit_python_env()
    audit_binaries()
    audit_dependencies()
    print("\nAudit completed.")

if __name__ == "__main__":
    main()

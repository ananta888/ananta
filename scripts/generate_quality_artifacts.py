import os
import sys
import subprocess
from pathlib import Path
import json
import time

ARTIFACTS_DIR = Path(".artifacts")

def run_and_save(command: list[str], filename: str):
    print(f"Running: {' '.join(command)}")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        output_file = ARTIFACTS_DIR / filename
        with open(output_file, "w") as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr)
        print(f"✅ Saved to {output_file}")
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error running {command[0]}: {e}")
        return False

def main():
    ARTIFACTS_DIR.mkdir(exist_ok=True)

    print("=== Generating Quality Artifacts ===")

    # 1. Cycles Report
    run_and_save([sys.executable, "scripts/check_cycles.py"], "cycles_report.txt")

    # 2. Dead Code Report
    run_and_save([sys.executable, "scripts/check_dead_code.py"], "dead_code_report.txt")

    # 3. Duplicates Report
    run_and_save([sys.executable, "scripts/check_duplicates.py"], "duplicates_report.txt")

    # 4. Runtime Audit
    run_and_save([sys.executable, "scripts/audit_runtime.py"], "runtime_audit.txt")

    # 5. Create a summary index
    summary = {
        "generated_at": time.time(),
        "artifacts": [str(p.name) for p in ARTIFACTS_DIR.glob("*.txt")]
    }
    with open(ARTIFACTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\nAll artifacts generated in .artifacts/")

if __name__ == "__main__":
    main()

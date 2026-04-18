import subprocess
import sys
import os

def analyze_imports():
    print("Analyzing import times for agent.ai_agent...")
    # Run python -X importtime to get detailed breakdown
    # The timing info goes to stderr
    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    cmd = [sys.executable, "-X", "importtime", "-c", "from agent.ai_agent import create_app"]
    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, env=env, text=True, encoding="utf-8")
    _, stderr = process.communicate()

    # Simple summary: find the top slowest imports
    lines = stderr.splitlines()
    imports = []
    for line in lines:
        if "import time:" in line:
            try:
                # Format: import time: self [us] | cumulative [us] | module
                parts = line.split("|")
                if len(parts) >= 3:
                    self_time = int(parts[0].replace("import time:", "").strip())
                    cumulative = int(parts[1].strip())
                    module = parts[2].strip()
                    imports.append((cumulative, self_time, module))
            except (ValueError, IndexError):
                continue

    # Sort by cumulative time
    imports.sort(key=lambda x: x[0], reverse=True)

    print("\nTop 20 slowest cumulative imports (including dependencies):")
    print(f"{'Cumul (ms)':>10} | {'Self (ms)':>10} | {'Module'}")
    print("-" * 60)
    for cum_us, self_us, module in imports[:20]:
        print(f"{cum_us/1000:>10.2f} | {self_us/1000:>10.2f} | {module}")

if __name__ == "__main__":
    analyze_imports()

import argparse
import subprocess
import sys
from typing import List, Optional


def run_command(command: List[str], cwd: Optional[str] = None) -> bool:
    print(f"Executing: {' '.join(command)}", flush=True)
    try:
        result = subprocess.run(command, cwd=cwd, check=False)
        return result.returncode == 0
    except Exception as e:
        print(f"Error executing command: {e}", flush=True)
        return False


def check_format():
    print("\n--- Checking Format (ruff) ---", flush=True)
    return run_command([sys.executable, "-m", "ruff", "format", "--check", "."])


def check_lint():
    print("\n--- Checking Lint (ruff) ---", flush=True)
    return run_command([sys.executable, "-m", "ruff", "check", "."])


def check_types():
    print("\n--- Checking Types (mypy) ---", flush=True)
    # For now, we only check the agent directory to avoid too many errors initially
    return run_command([sys.executable, "-m", "mypy", "agent"])


def check_arch():
    print("\n--- Checking Architecture Rules (BND-010/BND-011) ---", flush=True)
    return run_command([sys.executable, "scripts/check_imports.py"])


def check_cycles():
    print("\n--- Checking Import Cycles (CLN-020) ---", flush=True)
    return run_command([sys.executable, "scripts/check_cycles.py"])


def check_duplicates():
    print("\n--- Checking Code Duplicates (CLN-021) ---", flush=True)
    return run_command([sys.executable, "scripts/check_duplicates.py"])


def check_dead_code():
    print("\n--- Checking Dead Code (CLN-022) ---", flush=True)
    return run_command([sys.executable, "scripts/check_dead_code.py"])


def check_fast_tests():
    print("\n--- Running Fast Tests (pytest) ---", flush=True)
    # Running core unit tests, contract tests and smoke tests as fast tests
    fast_test_paths = [
        "tests/test_system_health_standard.py",
        "tests/test_task_state_machine_service.py",
        "tests/test_utils_extra.py",
        "tests/test_result_memory_service.py",
        "tests/test_api_contract_tasks.py",
        "tests/test_evolution_engine_contract.py",
    ]
    return run_command([sys.executable, "-m", "pytest"] + fast_test_paths)


def check_deep_tests():
    print("\n--- Running Deep Checks ---", flush=True)
    # Fixture repositories under tests/**/fixtures/** contain intentionally isolated
    # test files that are not importable from this repository root test run.
    # Verbose output makes CI hangs diagnosable by showing the last collected/running test.
    deep_test_command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-vv",
        "--durations=25",
        "-m",
        "not live_compose",
        "--ignore-glob=tests/**/fixtures/**",
    ]
    return run_command(deep_test_command)


def main():
    parser = argparse.ArgumentParser(description="Unified Check Pipeline for Ananta")
    parser.add_argument("--mode", choices=["fast", "standard", "deep"], default="standard", help="Check mode")
    parser.add_argument(
        "--skip-style",
        action="store_true",
        help="Skip repository-wide Ruff format/lint gates while preserving type, architecture, and test gates.",
    )
    parser.add_argument(
        "--skip-types",
        action="store_true",
        help="Skip repository-wide mypy gates while preserving architecture and test gates.",
    )
    args = parser.parse_args()

    success = True

    if args.mode in ["fast", "standard", "deep"] and not args.skip_style:
        if not check_format():
            success = False
        if not check_lint():
            success = False

    if args.mode in ["standard", "deep"]:
        if not args.skip_types:
            if not check_types():
                success = False
        if not check_arch():
            success = False
        if not check_cycles():
            success = False
        if not check_duplicates():
            success = False
        if not check_dead_code():
            success = False
        if not check_fast_tests():
            success = False

    if args.mode == "deep":
        # Run all tests except those that require a live compose environment.
        if not check_deep_tests():
            success = False

    if success:
        print("\n✅ All checks passed!", flush=True)
        sys.exit(0)
    else:
        print("\n❌ Some checks failed.", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

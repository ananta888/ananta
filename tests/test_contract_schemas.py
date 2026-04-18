import json
import os
from pathlib import Path
from agent.services.system_contract_service import SystemContractService

# We use the same baseline directory pattern but for schemas
BASELINE_DIR = Path(__file__).parent / "baselines" / "schemas"

def get_update_baselines():
    return os.environ.get("ANANTA_UPDATE_BASELINES", "false").lower() == "true" or os.environ.get("GENERATE_BASELINES") == "1"

def test_verify_contract_schemas():
    """
    Checks if the current JSON schemas from SystemContractService match the stored baselines.
    This fulfills CNT-031 by ensuring schema stability for core contracts.
    """
    service = SystemContractService()
    catalog = service.build_contract_catalog()
    schemas = catalog.get("schemas", {})

    assert len(schemas) > 0, "No schemas found in contract catalog"

    if not BASELINE_DIR.exists():
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    mismatches = []
    new_schemas = []

    for schema_name, schema_content in schemas.items():
        baseline_path = BASELINE_DIR / f"{schema_name}.json"

        # Sort keys for consistent comparison
        current_json = json.dumps(schema_content, indent=2, sort_keys=True)

        if not baseline_path.exists():
            if get_update_baselines():
                baseline_path.write_text(current_json, encoding="utf-8")
                new_schemas.append(schema_name)
            else:
                new_schemas.append(schema_name)
            continue

        stored_json = baseline_path.read_text(encoding="utf-8")

        if current_json != stored_json:
            if get_update_baselines():
                baseline_path.write_text(current_json, encoding="utf-8")
            else:
                mismatches.append(schema_name)

    error_msg = []
    if mismatches:
        error_msg.append(f"Schema mismatches detected in: {', '.join(mismatches)}")
        error_msg.append("If these changes are intentional, run with ANANTA_UPDATE_BASELINES=true to update baselines.")

    if new_schemas and not get_update_baselines():
        error_msg.append(f"New schemas without baselines detected: {', '.join(new_schemas)}")
        error_msg.append("Run with ANANTA_UPDATE_BASELINES=true to create baseline files.")

    if error_msg:
        assert False, "\n".join(error_msg)

if __name__ == "__main__":
    # Allow running directly to update baselines
    os.environ["ANANTA_UPDATE_BASELINES"] = "true"
    test_verify_contract_schemas()
    print("Contract schemas updated successfully.")

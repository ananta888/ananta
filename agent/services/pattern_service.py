import json
import os
from functools import lru_cache
from typing import List, Dict, Optional, Tuple

import jsonschema


class PatternService:
    _instance = None
    _catalog: Dict[str, dict] = {}

    def __init__(self, catalog_path: str, validator=None):
        self.catalog_path = catalog_path
        self.validator = validator
        self._load_catalog()

    def _load_catalog(self):
        if not os.path.exists(self.catalog_path):
            # If the catalog path does not exist, initialize an empty catalog
            # This case will primarily be for testing when a real catalog file hasn't been created yet
            # or for an empty initial state
            self._catalog = {}
            return

        with open(self.catalog_path, 'r') as f:
            data = json.load(f)
            # Assuming the catalog is a list of patterns
            self._catalog = {pattern['pattern_id']: pattern for pattern in data}

    def get(self, pattern_id: str) -> Optional[dict]:
        return self._catalog.get(pattern_id)

    def list(self, category: Optional[str] = None, language: Optional[str] = None) -> List[Dict]:
        filtered_patterns = []
        for pattern in self._catalog.values():
            match = True
            if category and pattern.get('category') != category:
                match = False
            if language and pattern.get('language') != language:
                match = False
            if match:
                filtered_patterns.append(pattern)
        return filtered_patterns

    def validate(self, payload: dict) -> Tuple[bool, List[str]]:
        if not self.validator:
            return False, ["Validator not initialized."]
        try:
            self.validator.validate(payload)
            return True, []
        except jsonschema.ValidationError as e:
            return False, [str(e)]
        except Exception as e:
            return False, [f"An unexpected error occurred during validation: {e}"]


@lru_cache(maxsize=1)
def get_pattern_service() -> PatternService:
    catalog_path = os.environ.get("ANANTA_PATTERN_CATALOG_PATH",
                                  "./schemas/patterns/pattern_catalog.v1.json")
    schema_path = os.environ.get("ANANTA_PATTERN_SCHEMA_PATH", "./schemas/patterns/pattern.schema.v1.json")

    # Ensure the schema file exists before trying to load it
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Pattern schema file not found at {schema_path}")

    with open(schema_path, 'r') as f:
        pattern_schema = json.load(f)
    
    # jsonschema.Draft7Validator is appropriate for '$schema': 'http://json-schema.org/draft-07/schema#'
    validator = jsonschema.Draft7Validator(pattern_schema)
    return PatternService(catalog_path, validator)



# Example usage for testing and demonstration outside the main application flow
if __name__ == '__main__':
    # Create a dummy schema and catalog for local testing
    dummy_schema_path = "./schemas/patterns/pattern.schema.v1.json"
    dummy_catalog_path = "./schemas/patterns/pattern_catalog.v1.json"

    # Ensure the directory exists
    os.makedirs(os.path.dirname(dummy_schema_path), exist_ok=True)

    # Write a minimal schema if it doesn't exist (for isolated run. This is a fallback)
    if not os.path.exists(dummy_schema_path):
        with open(dummy_schema_path, 'w') as f:
            f.write(json.dumps({
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": "Dummy Pattern Schema",
                "type": "object",
                "properties": {
                    "pattern_id": {"type": "string"},
                    "version": {"type": "string"},
                    "category": {"type": "string"},
                    "language": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "parameters": {"type": "array"},
                    "required_artifacts": {"type": "array"},
                    "steps": {"type": "array"},
                    "invariants": {"type": "array"},
                    "acceptance_gates": {"type": "array"},
                    "examples": {"type": "array"}
                },
                "required": ["pattern_id", "version", "category", "language", "title", "description", "parameters", "required_artifacts", "steps", "invariants", "acceptance_gates", "examples"]
            }, indent=2))

    # Write a dummy catalog file
    dummy_catalog_content = [
        {
            "pattern_id": "test_pattern_1",
            "version": "1.0.0",
            "category": "planning_renderer",
            "language": "python",
            "title": "Test Pattern One",
            "description": "A sample pattern for testing.",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        },
        {
            "pattern_id": "test_pattern_2",
            "version": "1.0.0",
            "category": "workflow_emit",
            "language": "java",
            "title": "Test Pattern Two",
            "description": "Another sample pattern.",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        },
        {
            "pattern_id": "test_pattern_3",
            "version": "1.0.0",
            "category": "planning_renderer",
            "language": "agnostic",
            "title": "Test Pattern Three",
            "description": "A third sample pattern.",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        }
    ]

    with open(dummy_catalog_path, 'w') as f:
        json.dump(dummy_catalog_content, f, indent=2)

    service = get_pattern_service()

    print("\n--- Listing all patterns ---")
    all_patterns = service.list()
    for p in all_patterns:
        print(f"  - {p.get('pattern_id')}")

    print("\n--- Listing 'planning_renderer' patterns ---")
    planning_patterns = service.list(category="planning_renderer")
    for p in planning_patterns:
        print(f"  - {p.get('pattern_id')}")

    print("\n--- Getting 'test_pattern_1' ---")
    pattern_1 = service.get("test_pattern_1")
    print(f"  Found: {pattern_1.get('title') if pattern_1 else 'None'}")

    print("\n--- Getting 'non_existent_pattern' ---")
    non_existent = service.get("non_existent_pattern")
    print(f"  Found: {non_existent.get('title') if non_existent else 'None'}")

    print("\n--- Validating a valid pattern ---")
    valid_payload = dummy_catalog_content[0]
    is_valid, errors = service.validate(valid_payload)
    print(f"  Is valid: {is_valid}, Errors: {errors}")

    print("\n--- Validating an invalid pattern (missing required field) ---")
    invalid_payload = {"pattern_id": "invalid_one", "version": "1.0.0"}
    is_valid, errors = service.validate(invalid_payload)
    print(f"  Is valid: {is_valid}, Errors: {errors}")

    # Clean up dummy files
    # os.remove(dummy_catalog_path)
    # os.remove(dummy_schema_path)


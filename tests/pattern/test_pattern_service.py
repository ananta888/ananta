import os
import json
import pytest
from unittest.mock import patch, mock_open
import jsonschema


# Assuming pattern_service is in agent/services
# The import path might need adjustment based on where pytest is run from
# For local execution within /home/krusty/ananta, this should be fine.
from agent.services.pattern_service import PatternService, get_pattern_service


# Define a minimal valid schema for testing purposes
@pytest.fixture
def temp_schema_file(tmp_path):
    schema_content = {
        "$id": "https://ananta.dev/schemas/patterns/pattern.schema.v1.json",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Dummy Pattern Schema",
        "type": "object",
        "properties": {
            "pattern_id": {"type": "string"},
            "version": {"type": "string"},
            "category": {"type": "string", "enum": ["cat1", "cat2", "planning_renderer"]},
            "language": {"type": "string", "enum": ["py", "java", "agnostic"]},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "array"},
            "required_artifacts": {"type": "array"},
            "steps": {"type": "array"},
            "invariants": {"type": "array"},
            "acceptance_gates": {"type": "array"},
            "examples": {"type": "array"}
        },
        "required": [
            "pattern_id", "version", "category", "language", "title", "description",
            "parameters", "required_artifacts", "steps", "invariants", "acceptance_gates", "examples"
        ]
    }
    schema_path = tmp_path / "pattern.schema.v1.json"
    with open(schema_path, 'w') as f:
        json.dump(schema_content, f)
    return str(schema_path)


# Define a sample catalog content for testing
@pytest.fixture
def sample_catalog_data():
    return [
        {
            "pattern_id": "test_pattern_1",
            "version": "1.0.0",
            "category": "cat1",
            "language": "py",
            "title": "Test Pattern One",
            "description": "Desc 1",
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
            "category": "cat2",
            "language": "java",
            "title": "Test Pattern Two",
            "description": "Desc 2",
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
            "category": "cat1",
            "language": "agnostic",
            "title": "Test Pattern Three",
            "description": "Desc 3",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        }
    ]


@pytest.fixture
def temp_catalog_file(tmp_path, sample_catalog_data):
    catalog_path = tmp_path / "pattern_catalog.v1.json"
    with open(catalog_path, 'w') as f:
        json.dump(sample_catalog_data, f)
    return str(catalog_path)


@pytest.fixture
def mock_pattern_service(temp_catalog_file, temp_schema_file):
    # Clear the lru_cache for get_pattern_service to ensure a fresh instance state for subsequent tests
    get_pattern_service.cache_clear()
    
    # Load the schema and create the validator using the actual temporary schema file
    with open(temp_schema_file, 'r') as f:
        schema = json.load(f)
    validator = jsonschema.Draft7Validator(schema)
    
    # Directly instantiate PatternService using the temporary files and the created validator
    return PatternService(temp_catalog_file, validator=validator)


class TestPatternService:

    def test_get_existing_pattern(self, mock_pattern_service, sample_catalog_data):
        pattern = mock_pattern_service.get("test_pattern_1")
        assert pattern is not None
        assert pattern["pattern_id"] == "test_pattern_1"
        assert pattern["title"] == "Test Pattern One"

    def test_get_missing_pattern(self, mock_pattern_service):
        pattern = mock_pattern_service.get("non_existent_pattern")
        assert pattern is None

    def test_list_all_patterns(self, mock_pattern_service, sample_catalog_data):
        patterns = mock_pattern_service.list()
        assert len(patterns) == len(sample_catalog_data)
        assert {p["pattern_id"] for p in patterns} == {"test_pattern_1", "test_pattern_2", "test_pattern_3"}

    def test_list_by_category(self, mock_pattern_service):
        patterns = mock_pattern_service.list(category="cat1")
        assert len(patterns) == 2
        assert {p["pattern_id"] for p in patterns} == {"test_pattern_1", "test_pattern_3"}

    def test_list_by_language(self, mock_pattern_service):
        patterns = mock_pattern_service.list(language="py")
        assert len(patterns) == 1
        assert patterns[0]["pattern_id"] == "test_pattern_1"

    def test_list_by_category_and_language(self, mock_pattern_service):
        patterns = mock_pattern_service.list(category="cat1", language="agnostic")
        assert len(patterns) == 1
        assert patterns[0]["pattern_id"] == "test_pattern_3"

    def test_list_no_match(self, mock_pattern_service):
        patterns = mock_pattern_service.list(category="non_existent_cat")
        assert len(patterns) == 0

    def test_validate_valid_payload(self, mock_pattern_service, sample_catalog_data):
        valid_payload = sample_catalog_data[0]
        is_valid, errors = mock_pattern_service.validate(valid_payload)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_invalid_payload_missing_required(self, mock_pattern_service):
        # Missing 'language' which is required based on the schema fixture
        invalid_payload = {
            "pattern_id": "invalid_one",
            "version": "1.0.0",
            "category": "cat1",
            "title": "Invalid Pattern",
            "description": "Missing language",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        }
        is_valid, errors = mock_pattern_service.validate(invalid_payload)
        assert is_valid is False
        assert any("'language' is a required property" in e for e in errors)

    def test_validate_invalid_payload_bad_enum_value(self, mock_pattern_service):
        # Invalid category value, not in ["cat1", "cat2", "planning_renderer"]
        invalid_payload = {
            "pattern_id": "invalid_enum",
            "version": "1.0.0",
            "category": "non_existent_category",  # Invalid enum value
            "language": "py",
            "title": "Invalid Enum Pattern",
            "description": "Bad category",
            "parameters": [],
            "required_artifacts": [],
            "steps": [],
            "invariants": [],
            "acceptance_gates": [],
            "examples": []
        }
        is_valid, errors = mock_pattern_service.validate(invalid_payload)
        assert is_valid is False
        assert any("is not one of" in e and "non_existent_category" in e for e in errors)

    @patch('os.path.exists')
    @patch.dict(os.environ)
    def test_get_pattern_service_singleton(
        self, mock_exists, temp_catalog_file, temp_schema_file
    ):
        get_pattern_service.cache_clear()

        # Configure mock_exists for the schema and catalog paths
        mock_exists.side_effect = lambda x: x == temp_schema_file or x == temp_catalog_file
        os.environ["ANANTA_PATTERN_CATALOG_PATH"] = temp_catalog_file
        os.environ["ANANTA_PATTERN_SCHEMA_PATH"] = temp_schema_file # Set schema path ENV var

        service1 = get_pattern_service()
        service2 = get_pattern_service()

        assert service1 is service2
        assert service1.catalog_path == temp_catalog_file
        assert isinstance(service1.get("test_pattern_1"), dict)
    
    @patch('os.path.exists')
    @patch.dict(os.environ)
    def test_get_pattern_service_initialization_with_nonexistent_catalog(
        self, mock_exists, tmp_path, temp_schema_file
    ):
        non_existent_catalog_path = str(tmp_path / "non_existent_catalog.json")
        get_pattern_service.cache_clear()

        # Configure mock_exists: schema file exists, non-existent catalog path does not
        def exists_side_effect(path):
            if path == temp_schema_file:
                return True
            elif path == non_existent_catalog_path:
                return False
            return False # Default for any other path
        mock_exists.side_effect = exists_side_effect

        os.environ["ANANTA_PATTERN_CATALOG_PATH"] = non_existent_catalog_path
        os.environ["ANANTA_PATTERN_SCHEMA_PATH"] = temp_schema_file # Set schema path ENV var

        service = get_pattern_service()
        assert service.catalog_path == non_existent_catalog_path
        assert service.list() == [] # Should be empty if catalog doesn't exist
        assert service.get("any_id") is None
    
    @patch('os.path.exists')
    @patch.dict(os.environ)
    def test_get_pattern_service_raises_if_schema_missing(
        self, mock_exists, tmp_path
    ):
        missing_schema_path = str(tmp_path / "missing_schema.json")
        get_pattern_service.cache_clear()

        # Configure mock_exists to return False for the missing schema path
        def exists_side_effect(path):
            if path == missing_schema_path:
                return False
            return True # Assume other paths exist for this test context
        mock_exists.side_effect = exists_side_effect
        
        # Also make sure the catalog path doesn't point to the missing schema just in case
        os.environ["ANANTA_PATTERN_CATALOG_PATH"] = str(tmp_path / "dummy_catalog.json") 
        os.environ["ANANTA_PATTERN_SCHEMA_PATH"] = missing_schema_path # Set schema path ENV var

        with pytest.raises(FileNotFoundError, match=f"Pattern schema file not found at {missing_schema_path}"):
            get_pattern_service()

    # Test case: catalog file exists, but it's empty JSON array
    @patch('os.path.exists')
    @patch.dict(os.environ)
    def test_pattern_service_with_empty_catalog(
        self, mock_exists, tmp_path, temp_schema_file
    ):
        empty_catalog_path = tmp_path / "empty_catalog.json"
        with open(empty_catalog_path, 'w') as f:
            f.write("[]") # Empty JSON array
        
        get_pattern_service.cache_clear()

        mock_exists.side_effect = lambda x: x == temp_schema_file or x == str(empty_catalog_path)
        os.environ["ANANTA_PATTERN_CATALOG_PATH"] = str(empty_catalog_path)
        os.environ["ANANTA_PATTERN_SCHEMA_PATH"] = temp_schema_file # Set schema path ENV var

        service = get_pattern_service()
        assert service.list() == []
        assert service.get("any_id") is None


    # Test case: catalog file exists, but it's invalid JSON
    @patch('os.path.exists') # Add patch for os.path.exists to this test as well
    @patch.dict(os.environ)
    def test_pattern_service_with_invalid_json_catalog(self, mock_exists, tmp_path, temp_schema_file):
        invalid_json_catalog_path = tmp_path / "invalid_json_catalog.json"
        with open(invalid_json_catalog_path, 'w') as f:
            f.write('{"key": "value"' ) # Intentionally invalid JSON, missing closing brace. Using single quotes to avoid Python literal issues.
        
        get_pattern_service.cache_clear()

        mock_exists.side_effect = lambda x: x == temp_schema_file or x == str(invalid_json_catalog_path)
        os.environ["ANANTA_PATTERN_CATALOG_PATH"] = str(invalid_json_catalog_path)
        os.environ["ANANTA_PATTERN_SCHEMA_PATH"] = temp_schema_file # Set schema path ENV var

        with pytest.raises(json.JSONDecodeError):
            get_pattern_service()

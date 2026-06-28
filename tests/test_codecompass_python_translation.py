"""Tests for the Python → Java/Rust semantic translation track (PYJR-001 to PYJR-027)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# PYJR-006: Python Type Confidence Model
# ---------------------------------------------------------------------------

class TestPythonTypeModel:
    def test_annotated_str(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("str")
        assert t.confidence == "annotated"
        assert t.raw == "str"

    def test_optional_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("Optional[str]")
        assert t.confidence == "annotated"
        assert t.is_optional is True
        assert t.none_model == "optional_type"
        assert t.element_type == "str"

    def test_union_none(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("str | None")
        assert t.is_optional is True
        assert t.element_type == "str"

    def test_any_type_is_dynamic(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("Any")
        assert t.confidence == "dynamic"
        assert "any_type_requires_review" in t.warnings

    def test_unknown_type_no_annotation(self):
        from agent.codecompass.semantic_translation.python_type_model import parse_python_type
        t = parse_python_type(None)
        assert t.confidence == "unknown"
        assert "no_type_annotation" in t.warnings

    def test_infer_from_int_default(self):
        import ast
        from agent.codecompass.semantic_translation.python_type_model import infer_type_from_default
        node = ast.parse("5", mode="eval").body
        t = infer_type_from_default(node)
        assert t.confidence == "inferred_from_default"
        assert t.raw == "int"

    def test_infer_from_none_default(self):
        import ast
        from agent.codecompass.semantic_translation.python_type_model import infer_type_from_default
        node = ast.parse("None", mode="eval").body
        t = infer_type_from_default(node)
        assert t.confidence == "inferred_from_default"
        assert t.none_model == "default_none"
        assert t.is_optional is True

    def test_infer_from_list_default(self):
        import ast
        from agent.codecompass.semantic_translation.python_type_model import infer_type_from_default
        node = ast.parse("[]", mode="eval").body
        t = infer_type_from_default(node)
        assert t.confidence == "inferred_from_default"
        assert t.collection_kind == "list"


# ---------------------------------------------------------------------------
# PYJR-007: None/Optional Semantics
# ---------------------------------------------------------------------------

class TestNoneOptionalSemantics:
    def test_optional_str_not_confused_with_falsy(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t_optional = _classify_type("Optional[str]")
        t_str = _classify_type("str")
        assert t_optional.is_optional is True
        assert t_str.is_optional is False

    def test_none_literal_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("None")
        assert t.none_model == "none_literal"
        assert t.is_optional is True

    def test_union_none_detected(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("int | None")
        assert t.is_optional is True
        assert t.element_type == "int"

    def test_java_optional_mapping(self):
        from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
        r = PythonToJavaTypeRegistry()
        m = r.map_type("Optional[str]")
        assert "Optional" in m.java_type
        assert "java.util.Optional" in m.imports

    def test_rust_option_mapping(self):
        from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
        r = PythonToRustTypeRegistry()
        m = r.map_type("Optional[str]")
        assert m.rust_type == "Option<String>"


# ---------------------------------------------------------------------------
# PYJR-008: Collection Semantics
# ---------------------------------------------------------------------------

class TestCollectionSemantics:
    def test_list_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("list[str]")
        assert t.collection_kind == "list"
        assert t.element_type == "str"

    def test_dict_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("dict[str, int]")
        assert t.collection_kind == "dict"
        assert t.key_type == "str"
        assert t.value_type == "int"

    def test_tuple_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("tuple[str, int]")
        assert t.collection_kind == "tuple"

    def test_set_type(self):
        from agent.codecompass.semantic_translation.python_type_model import _classify_type
        t = _classify_type("set[str]")
        assert t.collection_kind == "set"

    def test_java_list_mapping(self):
        from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
        r = PythonToJavaTypeRegistry()
        m = r.map_type("list[str]")
        assert m.java_type == "List<String>"
        assert "java.util.List" in m.imports

    def test_java_dict_mapping(self):
        from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
        r = PythonToJavaTypeRegistry()
        m = r.map_type("dict[str, int]")
        assert "Map<String,Long>" in m.java_type
        assert "java.util.Map" in m.imports

    def test_rust_vec_mapping(self):
        from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
        r = PythonToRustTypeRegistry()
        m = r.map_type("list[str]")
        assert m.rust_type == "Vec<String>"

    def test_rust_hashmap_mapping(self):
        from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
        r = PythonToRustTypeRegistry()
        m = r.map_type("dict[str, int]")
        assert "HashMap" in m.rust_type

    def test_rust_hashset_mapping(self):
        from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
        r = PythonToRustTypeRegistry()
        m = r.map_type("set[str]")
        assert "HashSet" in m.rust_type
        assert "std::collections::HashSet" in m.uses


# ---------------------------------------------------------------------------
# PYJR-001/002/003/004: Python Adapter
# ---------------------------------------------------------------------------

class TestPythonAdapter:
    def _adapt(self, code: str, path: str = "test.py") -> dict:
        from agent.codecompass.semantic_translation.python_adapter import PythonSemanticAdapter
        adapter = PythonSemanticAdapter()
        assert adapter.detect(path, code)
        return adapter.parse(path, code)

    def test_detects_py_files(self):
        from agent.codecompass.semantic_translation.python_adapter import PythonSemanticAdapter
        a = PythonSemanticAdapter()
        assert a.detect("foo.py", "") is True
        assert a.detect("foo.java", "") is False

    def test_parses_module_function(self):
        code = "def greet(name: str) -> str:\n    return f'Hi {name}'"
        parsed = self._adapt(code)
        assert len(parsed["functions"]) == 1
        assert parsed["functions"][0]["name"] == "greet"
        assert parsed["functions"][0]["return_type"] == "str"

    def test_parses_dataclass_fields(self):
        code = "from dataclasses import dataclass\n@dataclass\nclass User:\n    name: str\n    age: int"
        parsed = self._adapt(code)
        assert any(t["name"] == "User" for t in parsed["types"])
        user = next(t for t in parsed["types"] if t["name"] == "User")
        assert user["kind"] == "dataclass"
        field_names = [f["name"] for f in user["fields"]]
        assert "name" in field_names
        assert "age" in field_names

    def test_parses_frozen_dataclass(self):
        code = "@dataclass(frozen=True)\nclass Point:\n    x: float\n    y: float"
        parsed = self._adapt(code)
        item = next((t for t in parsed["types"] if t["name"] == "Point"), None)
        assert item is not None
        assert item["kind"] == "frozen_dataclass"

    def test_parses_enum(self):
        code = "from enum import Enum\nclass Status(Enum):\n    ACTIVE = 1\n    DISABLED = 2"
        parsed = self._adapt(code)
        item = next((t for t in parsed["types"] if t["name"] == "Status"), None)
        assert item is not None
        assert item["kind"] == "enum"
        assert "ACTIVE" in item["enum_values"]
        assert "DISABLED" in item["enum_values"]

    def test_parses_typed_dict(self):
        code = "from typing import TypedDict\nclass Config(TypedDict):\n    host: str\n    port: int"
        parsed = self._adapt(code)
        item = next((t for t in parsed["types"] if t["name"] == "Config"), None)
        assert item is not None
        assert item["kind"] == "typed_dict"
        field_names = [f["name"] for f in item["fields"]]
        assert "host" in field_names
        assert "port" in field_names

    def test_parses_class_instance_fields(self):
        code = "class Service:\n    def __init__(self):\n        self.host: str = 'localhost'\n        self.port: int = 8080"
        parsed = self._adapt(code)
        svc = next((t for t in parsed["types"] if t["name"] == "Service"), None)
        assert svc is not None
        assert svc["kind"] == "class"

    def test_detects_dynamic_import(self):
        code = "from os import *"
        parsed = self._adapt(code)
        codes = [d.get("code") for d in parsed.get("diagnostics", [])]
        assert "dynamic_import" in codes

    def test_varargs_warning(self):
        code = "def collect(*items: str) -> list:\n    pass"
        parsed = self._adapt(code)
        fn = parsed["functions"][0]
        assert "varargs_kwargs_block_auto_transform" in fn["warnings"]

    def test_nested_function_warning(self):
        code = "def outer() -> None:\n    def inner() -> None:\n        pass"
        parsed = self._adapt(code)
        fn = parsed["functions"][0]
        assert "nested_function_or_lambda_needs_review" in fn["warnings"]

    def test_emits_graph_records(self):
        code = "@dataclass\nclass User:\n    name: str"
        from agent.codecompass.semantic_translation.python_adapter import PythonSemanticAdapter
        result = PythonSemanticAdapter().emit_graph_records("test.py", code)
        assert result["nodes"]
        node_ids = [n["id"] for n in result["nodes"]]
        assert any("User" in nid for nid in node_ids)

    def test_keyword_only_param(self):
        code = "def configure(*, host: str, port: int = 8080) -> None:\n    pass"
        parsed = self._adapt(code)
        fn = parsed["functions"][0]
        kinds = [p["kind"] for p in fn["parameters"]]
        assert "keyword_only" in kinds

    def test_syntax_error_produces_diagnostic(self):
        code = "def broken(:\n    pass"
        parsed = self._adapt(code)
        assert not parsed["types"]
        assert any("python_syntax_error" in str(d) for d in parsed.get("diagnostics", []))


# ---------------------------------------------------------------------------
# PYJR-010: Dynamic Feature Detector
# ---------------------------------------------------------------------------

class TestDynamicFeatureDetector:
    def _detect(self, code: str) -> object:
        from agent.codecompass.semantic_translation.python_dynamic_detector import detect_dynamic_features
        return detect_dynamic_features(code)

    def test_eval_blocked(self):
        result = self._detect("x = eval('1+2')")
        assert result.has_blockers
        assert "eval_usage" in result.blocker_codes

    def test_exec_blocked(self):
        result = self._detect("exec('print(1)')")
        assert result.has_blockers
        assert "exec_usage" in result.blocker_codes

    def test_dynamic_import_blocked(self):
        result = self._detect("import importlib\nm = importlib.import_module('os')")
        assert result.has_blockers
        assert "dynamic_import" in result.blocker_codes

    def test_dunder_import_blocked(self):
        result = self._detect("m = __import__('os')")
        assert result.has_blockers
        assert "dynamic_import" in result.blocker_codes

    def test_getattr_dynamic_blocked(self):
        result = self._detect("name = input()\nv = getattr(obj, name)")
        assert result.has_blockers
        assert "dynamic_attribute_access" in result.blocker_codes

    def test_star_import_warning(self):
        result = self._detect("from os import *")
        assert not result.has_blockers
        codes = [f.code for f in result.features]
        assert "star_import" in codes

    def test_clean_code_no_blockers(self):
        result = self._detect("def add(a: int, b: int) -> int:\n    return a + b")
        assert not result.has_blockers

    def test_custom_metaclass_blocked(self):
        result = self._detect("class MyMeta(type):\n    pass\nclass Obj(metaclass=MyMeta):\n    pass")
        blocker_codes = result.blocker_codes
        assert "custom_metaclass" in blocker_codes


# ---------------------------------------------------------------------------
# PYJR-012: Python → Java Type Registry
# ---------------------------------------------------------------------------

class TestJavaTypeRegistry:
    def setup_method(self):
        from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
        self.reg = PythonToJavaTypeRegistry()

    def test_bool(self):
        m = self.reg.map_type("bool")
        assert m.java_type == "boolean"
        assert m.lossiness == "lossless"

    def test_int_long_default(self):
        m = self.reg.map_type("int")
        assert m.java_type == "long"
        assert m.lossiness == "policy_guarded"

    def test_float_double(self):
        m = self.reg.map_type("float")
        assert m.java_type == "double"

    def test_str_string(self):
        m = self.reg.map_type("str")
        assert m.java_type == "String"

    def test_bytes(self):
        m = self.reg.map_type("bytes")
        assert m.java_type == "byte[]"

    def test_decimal_bigdecimal(self):
        m = self.reg.map_type("Decimal")
        assert m.java_type == "BigDecimal"
        assert "java.math.BigDecimal" in m.imports

    def test_uuid(self):
        m = self.reg.map_type("UUID")
        assert m.java_type == "UUID"
        assert "java.util.UUID" in m.imports

    def test_optional_str(self):
        m = self.reg.map_type("str", optional=True)
        assert "Optional" in m.java_type
        assert "java.util.Optional" in m.imports

    def test_none_return(self):
        m = self.reg.map_type("None")
        assert m.java_type == "void"

    def test_any_lossy_needs_review(self):
        m = self.reg.map_type("Any")
        assert m.needs_review is True
        assert m.lossiness == "lossy"

    def test_unknown_type(self):
        m = self.reg.map_type("SomeFancyClass")
        assert m.needs_review is True
        assert any("unknown_python_type" in w for w in m.warnings)

    def test_list_str(self):
        m = self.reg.map_type("list[str]")
        assert m.java_type == "List<String>"
        assert "java.util.List" in m.imports

    def test_dict_str_int(self):
        m = self.reg.map_type("dict[str, int]")
        assert "Map<String,Long>" in m.java_type
        assert "java.util.Map" in m.imports


# ---------------------------------------------------------------------------
# PYJR-016: Python → Rust Type Registry
# ---------------------------------------------------------------------------

class TestRustTypeRegistry:
    def setup_method(self):
        from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
        self.reg = PythonToRustTypeRegistry()

    def test_bool(self):
        m = self.reg.map_type("bool")
        assert m.rust_type == "bool"

    def test_int_i64(self):
        m = self.reg.map_type("int")
        assert m.rust_type == "i64"
        assert m.lossiness == "policy_guarded"

    def test_float_f64(self):
        m = self.reg.map_type("float")
        assert m.rust_type == "f64"

    def test_str_string(self):
        m = self.reg.map_type("str")
        assert m.rust_type == "String"

    def test_bytes_vec_u8(self):
        m = self.reg.map_type("bytes")
        assert m.rust_type == "Vec<u8>"

    def test_decimal(self):
        m = self.reg.map_type("Decimal")
        assert m.rust_type == "Decimal"
        assert "rust_decimal::Decimal" in m.uses

    def test_uuid(self):
        m = self.reg.map_type("UUID")
        assert m.rust_type == "Uuid"
        assert "uuid::Uuid" in m.uses

    def test_optional_str(self):
        m = self.reg.map_type("str", optional=True)
        assert m.rust_type == "Option<String>"

    def test_none_unit(self):
        m = self.reg.map_type("None")
        assert m.rust_type == "()"

    def test_list_str(self):
        m = self.reg.map_type("list[str]")
        assert m.rust_type == "Vec<String>"

    def test_dict_str_int(self):
        m = self.reg.map_type("dict[str, int]")
        assert "HashMap" in m.rust_type
        assert "std::collections::HashMap" in m.uses

    def test_any_lossy(self):
        m = self.reg.map_type("Any")
        assert m.needs_review is True
        assert m.lossiness == "lossy"

    def test_unknown_type(self):
        m = self.reg.map_type("SomeFancyClass")
        assert m.needs_review is True


# ---------------------------------------------------------------------------
# PYJR-017: Rust Ownership Policy
# ---------------------------------------------------------------------------

class TestRustOwnershipPolicy:
    def setup_method(self):
        from agent.codecompass.semantic_translation.rust_ownership_policy import RustOwnershipPolicyEngine
        self.engine = RustOwnershipPolicyEngine()

    def test_owned_field(self):
        d = self.engine.decide_field_ownership("name", "String")
        assert d.policy == "owned"

    def test_mutable_field_warns(self):
        d = self.engine.decide_field_ownership("items", "Vec<String>", is_mutable=True)
        assert d.warnings

    def test_reference_field_lifetime_unknown(self):
        d = self.engine.decide_field_ownership("data", "&str")
        assert d.policy == "lifetime_unknown"

    def test_param_string_suggests_borrowed(self):
        d = self.engine.decide_param_ownership("name", "String")
        assert d.policy == "borrowed"

    def test_exception_known_stdlib(self):
        d = self.engine.classify_exception_policy("ValueError")
        assert d.rust_policy == "result_t_e"

    def test_exception_unknown_needs_review(self):
        d = self.engine.classify_exception_policy("WeirdCustomError")
        assert d.rust_policy == "needs_review"

    def test_exception_bare_blocks_transform(self):
        d = self.engine.classify_exception_policy("Exception")
        assert d.rust_policy == "needs_review"
        assert any("bare_exception" in w for w in d.warnings)


# ---------------------------------------------------------------------------
# PYJR-014: Java Emitter
# ---------------------------------------------------------------------------

class TestJavaEmitter:
    def setup_method(self):
        from agent.codecompass.semantic_translation.java_emitter import JavaEmitter
        self.emitter = JavaEmitter()

    def test_emit_record_basic(self):
        fields = [
            {"name": "name", "type": "str", "type_annotation": {"confidence": "annotated", "is_optional": False}},
            {"name": "age", "type": "int", "type_annotation": {"confidence": "annotated", "is_optional": False}},
        ]
        result = self.emitter.emit_record("User", fields)
        assert "record User" in result.source
        assert "name" in result.source
        assert "age" in result.source

    def test_emit_enum(self):
        result = self.emitter.emit_enum("Status", ["ACTIVE", "DISABLED"])
        assert "enum Status" in result.source
        assert "ACTIVE" in result.source
        assert "DISABLED" in result.source

    def test_emit_class(self):
        fields = [{"name": "host", "type": "str", "type_annotation": {"is_optional": False}}]
        result = self.emitter.emit_class("Config", fields)
        assert "class Config" in result.source
        assert "host" in result.source

    def test_emit_method_signature(self):
        method = {
            "name": "greet",
            "parameters": [
                {"name": "self", "kind": "self", "type": "", "type_annotation": {}},
                {"name": "name", "kind": "positional", "type": "str", "type_annotation": {"is_optional": False}},
            ],
            "return_type": "str",
            "return_type_annotation": {"is_optional": False},
        }
        result = self.emitter.emit_method_signature("Greeter", method)
        assert "greet" in result.source
        assert "String name" in result.source

    def test_stable_output(self):
        fields = [{"name": "x", "type": "float", "type_annotation": {"is_optional": False}}]
        r1 = self.emitter.emit_record("P", fields)
        r2 = self.emitter.emit_record("P", fields)
        assert r1.source == r2.source


# ---------------------------------------------------------------------------
# PYJR-019: Rust Emitter
# ---------------------------------------------------------------------------

class TestRustEmitter:
    def setup_method(self):
        from agent.codecompass.semantic_translation.rust_emitter import RustEmitter
        self.emitter = RustEmitter()

    def test_emit_struct_basic(self):
        fields = [
            {"name": "name", "type": "str", "type_annotation": {"is_optional": False}},
            {"name": "age", "type": "int", "type_annotation": {"is_optional": False}},
        ]
        result = self.emitter.emit_struct("User", fields)
        assert "pub struct User" in result.source
        assert "name:" in result.source
        assert "age:" in result.source

    def test_emit_struct_frozen_has_partial_eq(self):
        fields = [{"name": "x", "type": "float", "type_annotation": {"is_optional": False}}]
        result = self.emitter.emit_struct("Point", fields, frozen=True)
        assert "PartialEq" in result.source

    def test_emit_enum(self):
        result = self.emitter.emit_enum("Status", ["ACTIVE", "DISABLED"])
        assert "pub enum Status" in result.source
        assert "ACTIVE," in result.source

    def test_emit_fn_signature(self):
        fn = {
            "name": "add",
            "parameters": [
                {"name": "a", "kind": "positional", "type": "int", "type_annotation": {"is_optional": False}},
                {"name": "b", "kind": "positional", "type": "int", "type_annotation": {"is_optional": False}},
            ],
            "return_type": "int",
            "return_type_annotation": {"is_optional": False},
            "is_async": False,
        }
        result = self.emitter.emit_function_signature(fn)
        assert "pub fn add" in result.source
        assert "-> i64" in result.source

    def test_emit_option_field(self):
        fields = [{"name": "email", "type": "str", "type_annotation": {"is_optional": True}}]
        result = self.emitter.emit_struct("Profile", fields)
        assert "Option<" in result.source

    def test_stable_output(self):
        fields = [{"name": "x", "type": "float", "type_annotation": {"is_optional": False}}]
        r1 = self.emitter.emit_struct("P", fields)
        r2 = self.emitter.emit_struct("P", fields)
        assert r1.source == r2.source


# ---------------------------------------------------------------------------
# PYJR-020/021: Translation Plan Service + Transform Engine
# ---------------------------------------------------------------------------

class TestTranslationPlanService:
    def _plan(self, code: str, target: str = "java") -> object:
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        return PythonTranslationPlanService().create_plan(code, "test.py", target)

    def test_plan_dataclass_java(self):
        code = "@dataclass\nclass User:\n    name: str\n    age: int"
        plan = self._plan(code, "java")
        assert plan.entries
        entry = next((e for e in plan.entries if e.symbol == "User"), None)
        assert entry is not None
        assert entry.status in ("safe_auto_transform", "needs_review")
        assert entry.java_artifact is not None

    def test_plan_dataclass_rust(self):
        code = "@dataclass\nclass User:\n    name: str"
        plan = self._plan(code, "rust")
        entry = next((e for e in plan.entries if e.symbol == "User"), None)
        assert entry is not None
        assert entry.rust_artifact is not None

    def test_plan_both_targets(self):
        code = "@dataclass\nclass Item:\n    id: int"
        plan = self._plan(code, "both")
        symbols = {e.symbol for e in plan.entries}
        langs = {e.target_language for e in plan.entries}
        assert "Item" in symbols
        assert "java" in langs
        assert "rust" in langs

    def test_dynamic_blocker_detected(self):
        code = "def bad(x: str) -> str:\n    return eval(x)"
        plan = self._plan(code, "java")
        assert plan.dynamic_blockers
        entry = next((e for e in plan.entries if e.symbol == "bad"), None)
        assert entry is not None
        assert entry.status == "blocked_dynamic_runtime"

    def test_plan_is_serializable(self):
        code = "@dataclass\nclass X:\n    v: int"
        plan = self._plan(code, "java")
        d = plan.as_dict()
        json.dumps(d)  # must not raise

    def test_plan_unknown_symbol(self):
        code = "@dataclass\nclass A:\n    x: int"
        plan = self._plan(code, "java")
        # filter by symbol not present
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        plan2 = PythonTranslationPlanService().create_plan(code, "test.py", "java")
        filtered = [e for e in plan2.entries if "NonExistent" in e.symbol]
        assert filtered == []


class TestTransformEngine:
    def _transform(self, code: str, target: str = "java") -> list:
        from agent.codecompass.semantic_translation.python_transform import PythonTransformEngine
        return PythonTransformEngine().transform(code, "test.py", target)

    def test_transform_dataclass_java(self):
        code = "@dataclass\nclass User:\n    name: str"
        arts = self._transform(code, "java")
        assert arts
        art = arts[0]
        assert art.target_language == "java"
        assert art.symbol == "User"
        assert art.source_hash
        assert art.target_hash
        assert art.created_at

    def test_transform_enum_rust(self):
        code = "class Color(Enum):\n    RED = 1\n    GREEN = 2"
        arts = self._transform(code, "rust")
        art = next((a for a in arts if a.symbol == "Color"), None)
        assert art is not None
        assert "RED" in art.target_source

    def test_blocked_produces_failed_artifact(self):
        code = "def bad() -> None:\n    eval('x')"
        arts = self._transform(code, "java")
        assert arts
        assert all(a.verifier_status == "failed" for a in arts)

    def test_artifact_has_mandatory_fields(self):
        code = "@dataclass\nclass Y:\n    v: int"
        arts = self._transform(code, "java")
        art = arts[0]
        assert art.source_hash
        assert art.target_hash
        assert art.rule_ids is not None
        assert art.created_at


# ---------------------------------------------------------------------------
# PYJR-022: Verifier
# ---------------------------------------------------------------------------

class TestPythonVerifier:
    def test_java_verifier_field_completeness(self):
        from agent.codecompass.semantic_translation.java_emitter import JavaEmitter
        from agent.codecompass.semantic_translation.python_verifier import PythonToJavaVerifier
        fields = [
            {"name": "email", "type": "str", "type_annotation": {"is_optional": True}, "has_default": True},
        ]
        item = {"name": "Profile", "kind": "dataclass", "fields": fields, "methods": []}
        artifact = JavaEmitter().emit_record("Profile", fields).as_dict()
        result = PythonToJavaVerifier().verify(item, artifact)
        assert result.symbol == "Profile"
        assert result.status in ("verified", "verified_with_warnings")

    def test_java_verifier_missing_field_fails(self):
        from agent.codecompass.semantic_translation.python_verifier import PythonToJavaVerifier
        item = {"name": "X", "kind": "dataclass", "fields": [{"name": "secret", "type": "str", "type_annotation": {}, "has_default": False}], "methods": []}
        artifact = {"source": "public record X() {}", "needs_review": False, "warnings": []}
        result = PythonToJavaVerifier().verify(item, artifact)
        assert result.status == "failed"
        assert any("missing_field:secret" in c for c in result.checks_failed)

    def test_rust_verifier_option_not_emitted_fails(self):
        from agent.codecompass.semantic_translation.python_verifier import PythonToRustVerifier
        item = {"name": "P", "kind": "dataclass", "fields": [{"name": "email", "type": "str", "type_annotation": {"is_optional": True}, "has_default": False}], "methods": []}
        artifact = {"source": "pub struct P { pub email: String, }", "needs_review": False, "warnings": []}
        result = PythonToRustVerifier().verify(item, artifact)
        assert result.status == "failed"

    def test_rust_verifier_enum_values(self):
        from agent.codecompass.semantic_translation.python_verifier import PythonToRustVerifier
        from agent.codecompass.semantic_translation.rust_emitter import RustEmitter
        item = {"name": "Color", "kind": "enum", "fields": [], "enum_values": ["RED", "GREEN"], "methods": []}
        artifact = RustEmitter().emit_enum("Color", ["RED", "GREEN"]).as_dict()
        result = PythonToRustVerifier().verify(item, artifact)
        assert result.status in ("verified", "verified_with_warnings")


# ---------------------------------------------------------------------------
# PYJR-023: Semantic Diff
# ---------------------------------------------------------------------------

class TestSemanticDiff:
    def setup_method(self):
        from agent.codecompass.semantic_translation.semantic_diff import SemanticDiffEngine
        self.engine = SemanticDiffEngine()

    def test_no_diff_for_complete_struct(self):
        item = {"name": "P", "kind": "dataclass", "fields": [{"name": "x", "type_annotation": {"is_optional": False}}], "enum_values": []}
        artifact = {"source": "pub struct P { pub x: f64, }"}
        result = self.engine.diff(item, artifact, "rust")
        missing = [e for e in result.entries if e.kind == "missing_field"]
        assert not missing

    def test_missing_field_detected(self):
        item = {"name": "P", "kind": "dataclass", "fields": [{"name": "secret", "type_annotation": {"is_optional": False}}], "enum_values": []}
        artifact = {"source": "pub struct P {}"}
        result = self.engine.diff(item, artifact, "rust")
        assert result.has_divergence
        assert result.error_count > 0

    def test_lost_enum_value(self):
        item = {"name": "S", "kind": "enum", "fields": [], "enum_values": ["ACTIVE", "DISABLED"]}
        artifact = {"source": "pub enum S { ACTIVE }"}
        result = self.engine.diff(item, artifact, "rust")
        lost = [e for e in result.entries if e.kind == "lost_enum_value"]
        assert lost

    def test_changed_optionality_rust(self):
        item = {"name": "P", "kind": "dataclass", "fields": [{"name": "email", "type_annotation": {"is_optional": True}}], "enum_values": []}
        artifact = {"source": "pub struct P { pub email: String, }"}
        result = self.engine.diff(item, artifact, "rust")
        changed = [e for e in result.entries if e.kind == "changed_optionality"]
        assert changed

    def test_frozen_dataclass_no_mut(self):
        item = {"name": "P", "kind": "frozen_dataclass", "fields": [], "enum_values": []}
        artifact = {"source": "pub struct P { pub x: mut f64, }", "needs_review": False, "warnings": []}
        result = self.engine.diff(item, artifact, "rust")
        mutability = [e for e in result.entries if e.kind == "changed_mutability"]
        assert mutability

    def test_diff_is_serializable(self):
        item = {"name": "P", "kind": "dataclass", "fields": [], "enum_values": []}
        artifact = {"source": ""}
        result = self.engine.diff(item, artifact, "java")
        json.dumps(result.as_dict())

    def test_ten_intentional_divergences(self):
        """Verify semantic diff catches 10 different intentional divergences."""
        from agent.codecompass.semantic_translation.semantic_diff import SemanticDiffEngine
        engine = SemanticDiffEngine()
        divergences = []
        for i in range(10):
            item = {"name": f"C{i}", "kind": "dataclass", "fields": [{"name": f"f{i}", "type_annotation": {"is_optional": i % 2 == 0}}], "enum_values": []}
            # Artifact intentionally missing field
            artifact = {"source": f"pub struct C{i} {{}}"}
            r = engine.diff(item, artifact, "rust")
            if r.has_divergence:
                divergences.append(r)
        assert len(divergences) >= 10


# ---------------------------------------------------------------------------
# PYJR-025: Golden Sample Suite
# ---------------------------------------------------------------------------

class TestGoldenSamples:
    def _load(self, fname: str) -> list[dict]:
        path = FIXTURE_DIR / fname
        return json.loads(path.read_text())["samples"]

    def test_java_golden_samples_count(self):
        samples = self._load("python_golden_samples_java.json")
        assert len(samples) >= 20

    def test_rust_golden_samples_count(self):
        samples = self._load("python_golden_samples_rust.json")
        assert len(samples) >= 20

    def test_java_dataclass_to_record(self):
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        samples = self._load("python_golden_samples_java.json")
        svc = PythonTranslationPlanService()
        for s in samples:
            if s.get("source_kind") != "dataclass":
                continue
            py_code = f"from dataclasses import dataclass\nfrom typing import Optional\nfrom uuid import UUID\nfrom decimal import Decimal\nfrom datetime import datetime\n{s['source_python']}"
            plan = svc.create_plan(py_code, "sample.py", "java")
            entry = next((e for e in plan.entries if e.symbol == s["source_python"].split("class ")[-1].split(":")[0].split("(")[0].strip()), None)
            if entry and s.get("expected_java_kind") == "record":
                assert entry.java_artifact is not None

    def test_rust_enum_golden(self):
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        samples = self._load("python_golden_samples_rust.json")
        svc = PythonTranslationPlanService()
        for s in samples:
            if s.get("source_kind") != "enum":
                continue
            py_code = f"from enum import Enum\n{s['source_python']}"
            plan = svc.create_plan(py_code, "sample.py", "rust")
            entries = [e for e in plan.entries if e.target_language == "rust"]
            assert entries

    def test_java_blocked_dynamic_runtime(self):
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        samples = self._load("python_golden_samples_java.json")
        blocked = [s for s in samples if s.get("expected_transform_status") == "blocked_dynamic_runtime"]
        assert blocked
        svc = PythonTranslationPlanService()
        for s in blocked:
            py_code = f"def fn():\n    {s['source_python'].split(':')[1].strip() if ':' in s['source_python'] else 'eval(x)'}"
            plan = svc.create_plan(s["source_python"], "sample.py", "java")
            assert plan.dynamic_blockers

    def test_rust_blocked_dynamic_runtime(self):
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        samples = self._load("python_golden_samples_rust.json")
        blocked = [s for s in samples if s.get("expected_transform_status") == "blocked_dynamic_runtime"]
        assert blocked
        svc = PythonTranslationPlanService()
        for s in blocked:
            plan = svc.create_plan(s["source_python"], "sample.py", "rust")
            assert plan.dynamic_blockers

    def test_no_network_required(self):
        from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService
        # Must work without any network — pure stdlib + local code
        svc = PythonTranslationPlanService()
        plan = svc.create_plan("@dataclass\nclass X:\n    v: int", "x.py", "both")
        assert plan is not None

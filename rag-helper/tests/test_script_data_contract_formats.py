from __future__ import annotations

import json

from rag_helper.extractors.data_contract_extractor import (
    GraphqlExtractor,
    JsonDocumentExtractor,
    ProtoExtractor,
    SqlExtractor,
    TerraformExtractor,
)
from rag_helper.extractors.script_extractor import PowerShellExtractor, ShellScriptExtractor


def test_shell_extracts_symbols_sources_calls_and_eval_diagnostic_without_command_execution() -> None:
    marker = "COMMAND_SUBSTITUTION_MUST_STAY_TEXT"
    index, details, relations, stats = ShellScriptExtractor().parse(
        "scripts/run.sh",
        (
            "#!/usr/bin/env bash\n"
            "MODE=dev\n"
            "build() {\n"
            '  ./prepare.sh "$1"\n'
            "}\n"
            "source ./lib/common.sh\n"
            f"VALUE=$(printf {marker})\n"
            'eval "$VALUE"\n'
        ),
    )
    assert index[0]["parser_mode"] == "regex_fallback"
    assert index[0]["confidence"] == 0.55
    assert any(item["kind"] == "shell_function" and item["name"] == "build" for item in details)
    assert any(item["kind"] == "shell_variable" and item["name"] == "VALUE" for item in details)
    assert {item["relation"] for item in relations} >= {"sources_script", "calls_local_script"}
    assert any(item.get("code") == "shell_dynamic_eval" for item in details)
    assert stats["diagnostic_codes"] == ["shell_dynamic_eval"]


def test_powershell_extracts_functions_params_imports_and_dynamic_expression_diagnostic() -> None:
    index, details, relations, stats = PowerShellExtractor().parse(
        "scripts/Deploy.ps1",
        (
            "param(\n"
            "  [Parameter(Mandatory)] $Environment\n"
            ")\n"
            "function Invoke-Deploy { $local = 1 }\n"
            "Import-Module ./DeployTools.psm1\n"
            ". ./Common.ps1\n"
            "& ./Child.ps1\n"
            "Invoke-Expression $command\n"
        ),
    )
    assert index[0]["parser_mode"] == "regex_fallback"
    assert any(item["kind"] == "powershell_function" and item["name"] == "Invoke-Deploy" for item in details)
    assert any(item["kind"] == "powershell_parameter" and item["name"] == "Environment" for item in details)
    assert {item["relation"] for item in relations} >= {"imports_module", "sources_script", "calls_local_script"}
    assert any(item.get("code") == "powershell_dynamic_expression" for item in details)
    assert stats["confidence"] == 0.55


def test_json_schema_extracts_definitions_properties_required_refs_and_composition() -> None:
    index, details, relations, stats = JsonDocumentExtractor().parse(
        "schemas/order.schema.json",
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:order",
                "$defs": {
                    "Money": {
                        "type": "object",
                        "properties": {"amount": {"type": "number"}},
                        "required": ["amount"],
                    }
                },
                "properties": {"total": {"$ref": "#/$defs/Money"}},
                "allOf": [{"type": "object"}],
            },
            indent=2,
        ),
    )
    amount = next(item for item in details if item.get("kind") == "json_schema_property" and item["name"] == "amount")
    reference = next(item for item in relations if item["relation"] == "references_schema")
    assert index[0]["summary"]["schema_id"] == "urn:order"
    assert amount["required"] is True
    assert reference["resolution_status"] == "resolved"
    assert any(item["relation"] == "composes_allOf" for item in relations)
    assert stats["definition_count"] == 1


def test_sql_extracts_tables_columns_foreign_keys_views_and_indexes() -> None:
    index, details, relations, stats = SqlExtractor().parse(
        "db/schema.sql",
        (
            "CREATE TABLE customers (id bigint PRIMARY KEY);\n"
            "CREATE TABLE orders (\n"
            "  id bigint PRIMARY KEY,\n"
            "  customer_id bigint NOT NULL REFERENCES customers(id),\n"
            "  total numeric(10, 2)\n"
            ");\n"
            "CREATE VIEW order_totals AS SELECT id, total FROM orders;\n"
            "CREATE UNIQUE INDEX orders_customer_idx ON orders(customer_id);\n"
        ),
    )
    assert index[0]["summary"]["table_count"] == 2
    assert stats["view_count"] == 1
    assert stats["sql_index_count"] == 1
    assert {item["name"] for item in details if item["kind"] == "sql_column"} >= {"id", "customer_id", "total"}
    assert any(
        item["relation"] == "references_table"
        and item["target"] == "customers"
        and item["resolution_status"] == "resolved"
        for item in relations
    )
    assert any(item["relation"] == "reads_from_relation" and item["target"] == "orders" for item in relations)
    assert any(item["relation"] == "indexes_table" and item["target"] == "orders" for item in relations)


def test_proto_extracts_messages_fields_services_rpcs_and_imports() -> None:
    index, details, relations, stats = ProtoExtractor().parse(
        "contracts/order.proto",
        (
            'syntax = "proto3";\n'
            "package shop;\n"
            'import "money.proto";\n'
            "message Order { string id = 1; Money total = 2; }\n"
            "message GetOrder { string id = 1; }\n"
            "service Orders { rpc Get(GetOrder) returns (Order); }\n"
        ),
    )
    assert index[0]["summary"]["message_count"] == 2
    assert stats["service_count"] == 1
    assert any(item["kind"] == "proto_field" and item["name"] == "total" for item in details)
    assert any(item["kind"] == "proto_rpc" and item["name"] == "Get" for item in details)
    assert any(item["relation"] == "imports_proto" and item["target"] == "money.proto" for item in relations)
    assert any(item["relation"] == "returns_type" and item["resolution_status"] == "resolved" for item in relations)


def test_graphql_extracts_types_fields_operations_unions_and_imports() -> None:
    index, details, relations, stats = GraphqlExtractor().parse(
        "contracts/schema.graphql",
        (
            '# import "money.graphql"\n'
            "interface Node { id: ID! }\n"
            "type Order implements Node { id: ID!, total: Money! }\n"
            "type Query { order(id: ID!): Order }\n"
            "union SearchResult = Order | Money\n"
            'query FindOrder { order(id: "1") { id } }\n'
        ),
    )
    assert index[0]["summary"]["field_count"] >= 3
    assert stats["operation_count"] == 1
    assert any(item["kind"] == "graphql_union" for item in details)
    assert any(item["relation"] == "implements_type" and item["target"] == "Node" for item in relations)
    assert any(item["relation"] == "imports_graphql" for item in relations)


def test_terraform_extracts_blocks_module_sources_and_object_references_without_evaluation() -> None:
    index, details, relations, stats = TerraformExtractor().parse(
        "infra/main.tf",
        (
            'resource "aws_s3_bucket" "assets" { bucket = "assets" }\n'
            'resource "aws_s3_bucket_policy" "assets" { bucket = aws_s3_bucket.assets.id }\n'
            'module "network" { source = "./modules/network" }\n'
            'variable "region" { type = string }\n'
        ),
    )
    assert index[0]["summary"]["resource_count"] == 2
    assert stats["module_count"] == 1
    assert any(
        item["relation"] == "loads_module_source" and item["target"] == "./modules/network" for item in relations
    )
    assert any(
        item["relation"] == "references_terraform_object" and item["target"] == "aws_s3_bucket.assets"
        for item in relations
    )
    assert all(item.get("executed") is not True for item in details)

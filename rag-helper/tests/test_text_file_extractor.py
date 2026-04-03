from __future__ import annotations

import unittest

from rag_helper.application.summary_records import build_summary_records
from rag_helper.extractors.text_file_extractor import TextFileExtractor


class TextFileExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = TextFileExtractor(embedding_text_mode="compact")

    def test_parses_markdown_headings(self) -> None:
        index_records, detail_records, relation_records, stats = self.extractor.parse(
            "docs/readme.md",
            "# Intro\nSome text\n## Usage\nMore text\n",
        )
        self.assertEqual(index_records[0]["kind"], "md_file")
        self.assertEqual(detail_records[0]["kind"], "md_section")
        self.assertEqual(detail_records[0]["heading"], "Intro")
        self.assertEqual(relation_records[0]["type"], "contains_section")
        self.assertEqual(stats["heading_count"], 2)

    def test_parses_python_and_typescript_symbols(self) -> None:
        py_index, py_details, _, py_stats = self.extractor.parse(
            "src/app.py",
            (
                "import os\n"
                "from pkg.base import Base\n\n"
                "@service\n"
                "class Service(Base):\n"
                "    @classmethod\n"
                "    def build(cls):\n"
                "        return cls()\n\n"
                "async def run():\n"
                "    return 1\n"
            ),
        )
        ts_index, ts_details, _, ts_stats = self.extractor.parse(
            "src/app.ts",
            (
                "import { Base } from './base';\n"
                "@injectable()\n"
                "export class Service extends Base {\n"
                "  @trace\n"
                "  async run(): Promise<void> {}\n"
                "}\n"
                "export function helper() {}\n"
            ),
        )
        self.assertEqual(py_index[0]["kind"], "python_file")
        self.assertEqual(py_index[0]["summary"]["parse_mode"], "ast")
        self.assertEqual(py_index[0]["summary"]["import_count"], 2)
        self.assertEqual(py_index[0]["classes"][0]["bases"], ["Base"])
        self.assertEqual(py_index[0]["classes"][0]["methods"][0]["name"], "build")
        self.assertEqual(py_index[0]["functions"][0]["name"], "run")
        self.assertEqual(py_index[0]["summary"]["method_count"], 1)
        self.assertEqual(py_stats["symbol_count"], 2)
        self.assertEqual(ts_index[0]["kind"], "typescript_file")
        self.assertEqual(ts_index[0]["summary"]["parse_mode"], "heuristic")
        self.assertEqual(ts_index[0]["summary"]["import_count"], 1)
        self.assertEqual(ts_details[1]["extends"], "Base")
        self.assertEqual(ts_details[1]["decorators"], ["@injectable"])
        self.assertEqual(ts_details[2]["kind"], "typescript_method")
        self.assertEqual(ts_details[2]["decorators"], ["@trace"])
        self.assertEqual(ts_stats["symbol_count"], 3)

    def test_parses_yaml_properties_and_sql(self) -> None:
        yaml_index, yaml_details, _, yaml_stats = self.extractor.parse(
            "config/app.yaml",
            "server:\n  port: 8080\nfeatureFlag: true\n",
        )
        properties_index, properties_details, _, properties_stats = self.extractor.parse(
            "config/app.properties",
            "app.name=demo\nfeature.enabled=true\n",
        )
        sql_index, sql_details, sql_relations, sql_stats = self.extractor.parse(
            "db/schema.sql",
            "create table users (id int);\ninsert into users values (1);\n",
        )
        self.assertEqual(yaml_index[0]["kind"], "yaml_file")
        self.assertEqual(yaml_details[0]["key"], "server")
        self.assertEqual(yaml_stats["entry_count"], 3)
        self.assertEqual(properties_index[0]["kind"], "properties_file")
        self.assertEqual(properties_details[0]["key"], "app.name")
        self.assertEqual(properties_stats["entry_count"], 2)
        self.assertEqual(sql_index[0]["kind"], "sql_file")
        self.assertEqual(sql_details[0]["kind"], "sql_statement")
        self.assertEqual(sql_relations[0]["type"], "contains_statement")
        self.assertEqual(sql_stats["statement_count"], 2)

    def test_parses_angular_typescript_with_multiline_component_metadata(self) -> None:
        index_records, detail_records, _, stats = self.extractor.parse(
            "src/app/app.component.ts",
            (
                "import { Component } from '@angular/core';\n\n"
                "@Component({\n"
                "  selector: 'app-root',\n"
                "  templateUrl: './app.component.html',\n"
                "  standalone: true,\n"
                "  imports: [CommonModule, RouterOutlet],\n"
                "})\n"
                "export class AppComponent {\n"
                "  ngOnInit(): void {}\n"
                "}\n"
            ),
        )

        file_record = index_records[0]
        component_record = next(record for record in detail_records if record["kind"] == "typescript_class")
        lifecycle_record = next(record for record in detail_records if record["kind"] == "typescript_method")

        self.assertEqual(file_record["frameworks"], ["angular"])
        self.assertIn("angular_component", file_record["framework_roles"])
        self.assertEqual(file_record["summary"]["component_count"], 1)
        self.assertEqual(component_record["framework"], "angular")
        self.assertEqual(component_record["framework_role"], "angular_component")
        self.assertEqual(component_record["decorators"], ["@Component"])
        self.assertEqual(component_record["angular_metadata"]["selector"], "app-root")
        self.assertEqual(component_record["angular_metadata"]["template_url"], "./app.component.html")
        self.assertTrue(component_record["angular_metadata"]["standalone"])
        self.assertEqual(component_record["angular_metadata"]["imports"], ["CommonModule", "RouterOutlet"])
        self.assertEqual(lifecycle_record["framework_role"], "angular_lifecycle")
        self.assertEqual(stats["framework_count"], 1)

    def test_parses_react_typescript_components_hooks_and_folder_summary(self) -> None:
        component_index, component_details, _, component_stats = self.extractor.parse(
            "src/ui/App.tsx",
            (
                "import React, { useEffect, useState } from 'react';\n\n"
                "export function App() {\n"
                "  const [ready, setReady] = useState(false);\n"
                "  useEffect(() => setReady(true), []);\n"
                "  return <main>{ready ? 'ok' : 'wait'}</main>;\n"
                "}\n"
            ),
        )
        hook_index, hook_details, _, _ = self.extractor.parse(
            "src/ui/useWidget.ts",
            (
                "import { useEffect } from 'react';\n\n"
                "export function useWidget() {\n"
                "  useEffect(() => {}, []);\n"
                "}\n"
            ),
        )

        app_record = next(record for record in component_details if record["kind"] == "typescript_function")
        hook_record = next(record for record in hook_details if record["kind"] == "typescript_function")
        summaries, summary_stats = build_summary_records(
            [component_index[0], hook_index[0]],
            embedding_text_mode="compact",
        )
        folder_summary = next(record for record in summaries if record["kind"] == "typescript_folder_summary")

        self.assertEqual(component_index[0]["frameworks"], ["react"])
        self.assertIn("react_component", component_index[0]["framework_roles"])
        self.assertEqual(component_index[0]["hook_calls"], ["useEffect", "useState"])
        self.assertEqual(app_record["framework"], "react")
        self.assertEqual(app_record["framework_role"], "react_component")
        self.assertEqual(app_record["hook_calls"], ["useEffect", "useState"])
        self.assertEqual(hook_record["framework_role"], "react_hook")
        self.assertEqual(component_stats["component_count"], 1)
        self.assertEqual(folder_summary["frameworks"], ["react"])
        self.assertEqual(folder_summary["summary"]["framework_count"], 1)
        self.assertEqual(folder_summary["summary"]["component_count"], 1)
        self.assertEqual(folder_summary["summary"]["hook_count"], 1)
        self.assertEqual(summary_stats["typescript_folder_summary_count"], 1)

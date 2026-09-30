"""Detection of ``it``/``test`` cases and fixture usage in TypeScript test files."""

from __future__ import annotations

import re

from rag_helper.utils.ids import safe_id

TYPESCRIPT_TEST_CASE_PATTERN = re.compile(
    r"^\s*(?P<call_kind>it|test)(?:\.(?:only|skip|todo))?\s*\(\s*"
    r"(?P<quote>['\"])(?P<name>.*?)(?P=quote)\s*,"
)
TYPESCRIPT_FIXTURE_ARGUMENT_PATTERN = re.compile(
    r"(?:async\s*)?\(\s*\{(?P<fixtures>[^}]+)\}\s*\)\s*=>"
)


class TypeScriptTestCaseCollector:
    """Append ``typescript_call`` test-case records and fixture relations."""

    def append_test_records(
        self,
        *,
        rel_path: str,
        lines: list[str],
        file_id: str,
        details: list[dict],
        relations: list[dict],
    ) -> int:
        if not self._is_typescript_test_path(rel_path):
            return 0
        count = 0
        for index, raw in enumerate(lines):
            match = TYPESCRIPT_TEST_CASE_PATTERN.match(raw)
            if match is None:
                continue
            count += 1
            line_start = index + 1
            line_end = self._typescript_call_end_line(lines, index)
            test_name = match.group("name").strip()
            test_id = (
                f"typescript_call:"
                f"{safe_id(rel_path, match.group('call_kind'), test_name, str(count))}"
            )
            record = {
                "kind": "typescript_call",
                "record_kind": "test_case",
                "file": rel_path,
                "id": test_id,
                "parent_id": file_id,
                "name": test_name,
                "call_kind": match.group("call_kind"),
                "line": line_start,
                "end_line": line_end,
            }
            details.append(record)
            relations.append(
                {
                    "kind": "relation",
                    "file": rel_path,
                    "id": f"relation:{safe_id(rel_path, file_id, 'contains_test_case', test_name)}",
                    "from": file_id,
                    "to": test_id,
                    "type": "contains_test_case",
                    "source_id": file_id,
                    "source_kind": "typescript_file",
                    "source_name": rel_path,
                    "relation": "contains_test_case",
                    "target": test_name,
                    "target_resolved": test_id,
                    "resolution_status": "resolved",
                    "weight": 1,
                    "line": line_start,
                }
            )

            statement = "\n".join(lines[index:line_end])
            fixture_match = TYPESCRIPT_FIXTURE_ARGUMENT_PATTERN.search(statement)
            if fixture_match is None:
                continue
            for fixture_name in self._typescript_fixture_names(fixture_match.group("fixtures")):
                relations.append(
                    {
                        "kind": "relation",
                        "record_kind": "fixture_relation",
                        "file": rel_path,
                        "id": f"fixture_relation:{safe_id(rel_path, test_id, fixture_name)}",
                        "record_id": (
                            f"fixture_relation:{safe_id(rel_path, test_name, fixture_name)}"
                        ),
                        "from": test_id,
                        "to": fixture_name,
                        "type": "uses_fixture",
                        "source_id": test_id,
                        "source_kind": "typescript_call",
                        "source_name": test_name,
                        "relation": "uses_fixture",
                        "target": fixture_name,
                        "target_resolved": None,
                        "resolution_status": "unresolved",
                        "weight": 1,
                        "line": line_start,
                    }
                )
        return count

    @staticmethod
    def _is_typescript_test_path(rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/").lower()
        basename = normalized.rsplit("/", 1)[-1]
        return (
            basename.endswith((".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx"))
            or "/tests/" in f"/{normalized}"
        )

    @staticmethod
    def _typescript_call_end_line(lines: list[str], start_index: int) -> int:
        balance = 0
        seen_open = False
        for index in range(start_index, min(len(lines), start_index + 200)):
            raw = lines[index]
            balance += raw.count("(") - raw.count(")")
            seen_open = seen_open or "(" in raw
            if seen_open and balance <= 0:
                return index + 1
        return start_index + 1

    @staticmethod
    def _typescript_fixture_names(raw: str) -> list[str]:
        fixtures: list[str] = []
        for item in raw.split(","):
            candidate = item.strip().split(":", 1)[0].split("=", 1)[0].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", candidate):
                fixtures.append(candidate)
        return list(dict.fromkeys(fixtures))

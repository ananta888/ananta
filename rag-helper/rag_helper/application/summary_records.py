from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


def build_summary_records(index_records: list[dict], embedding_text_mode: str) -> tuple[list[dict], dict]:
    summary_records = [
        *build_java_package_summaries(index_records, embedding_text_mode),
        *build_python_module_summaries(index_records, embedding_text_mode),
        *build_typescript_folder_summaries(index_records, embedding_text_mode),
    ]
    return summary_records, {
        "summary_record_count": len(summary_records),
        "java_package_summary_count": sum(1 for record in summary_records if record["kind"] == "java_package_summary"),
        "python_module_summary_count": sum(1 for record in summary_records if record["kind"] == "python_module_summary"),
        "typescript_folder_summary_count": sum(1 for record in summary_records if record["kind"] == "typescript_folder_summary"),
    }


def build_java_package_summaries(index_records: list[dict], embedding_text_mode: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in index_records:
        if record.get("kind") != "java_file":
            continue
        grouped[record.get("package") or "default"].append(record)

    summaries: list[dict] = []
    for package_name, records in sorted(grouped.items()):
        type_names: list[str] = []
        imports_count = 0
        method_count = 0
        constructor_count = 0
        file_paths = sorted(record["file"] for record in records)
        for record in records:
            imports_count += len(record.get("imports", []))
            summary = record.get("summary", {})
            method_count += summary.get("method_count", 0)
            constructor_count += summary.get("constructor_count", 0)
            for type_info in record.get("types", []):
                name = type_info.get("name")
                if name:
                    type_names.append(name)

        summaries.append({
            "kind": "java_package_summary",
            "id": f"java_package_summary:{safe_id(package_name)}",
            "package": package_name,
            "file": package_name,
            "files": file_paths[:50],
            "type_names": type_names[:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                (
                    f"Java package {package_name}. "
                    f"Files: {', '.join(file_paths[:20]) or 'none'}. "
                    f"Types: {', '.join(type_names[:30]) or 'none'}. "
                    f"Imports {imports_count}. Methods {method_count}. Constructors {constructor_count}."
                ),
                (
                    f"Java package {package_name}. "
                    f"Files {len(file_paths)}. Types {compact_list(type_names, limit=6)}. "
                    f"Methods {method_count}."
                ),
            ),
            "summary": {
                "file_count": len(file_paths),
                "type_count": len(type_names),
                "imports_count": imports_count,
                "method_count": method_count,
                "constructor_count": constructor_count,
            },
        })
    return summaries


def build_python_module_summaries(index_records: list[dict], embedding_text_mode: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in index_records:
        if record.get("kind") != "python_file":
            continue
        grouped[_python_module_area(record["file"])].append(record)

    summaries: list[dict] = []
    for module_area, records in sorted(grouped.items()):
        file_paths = sorted(record["file"] for record in records)
        imports: list[str] = []
        class_names: list[str] = []
        function_names: list[str] = []
        method_count = 0
        import_count = 0
        for record in records:
            summary = record.get("summary", {})
            method_count += summary.get("method_count", 0)
            import_count += summary.get("import_count", 0)
            imports.extend(record.get("imports", []))
            for class_info in record.get("classes", []):
                name = class_info.get("name")
                if name:
                    class_names.append(name)
            for function_info in record.get("functions", []):
                name = function_info.get("name")
                if name:
                    function_names.append(name)

        summaries.append({
            "kind": "python_module_summary",
            "id": f"python_module_summary:{safe_id(module_area)}",
            "module_area": module_area,
            "file": module_area,
            "files": file_paths[:50],
            "imports": imports[:50],
            "classes": class_names[:50],
            "functions": function_names[:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                (
                    f"Python module area {module_area}. "
                    f"Files: {', '.join(file_paths[:20]) or 'none'}. "
                    f"Classes: {', '.join(class_names[:20]) or 'none'}. "
                    f"Functions: {', '.join(function_names[:20]) or 'none'}. "
                    f"Imports {import_count}. Methods {method_count}."
                ),
                (
                    f"Python module area {module_area}. "
                    f"Files {len(file_paths)}. Classes {compact_list(class_names, limit=6)}. "
                    f"Functions {compact_list(function_names, limit=6)}."
                ),
            ),
            "summary": {
                "file_count": len(file_paths),
                "import_count": import_count,
                "class_count": len(class_names),
                "function_count": len(function_names),
                "method_count": method_count,
            },
        })
    return summaries


def build_typescript_folder_summaries(index_records: list[dict], embedding_text_mode: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in index_records:
        if record.get("kind") != "typescript_file":
            continue
        grouped[_folder_area(record["file"])].append(record)

    summaries: list[dict] = []
    for folder, records in sorted(grouped.items()):
        file_paths = sorted(record["file"] for record in records)
        imports: list[str] = []
        symbol_names: list[str] = []
        method_count = 0
        import_count = 0
        for record in records:
            summary = record.get("summary", {})
            method_count += summary.get("method_count", 0)
            import_count += summary.get("import_count", 0)
            imports.extend(record.get("imports", []))
            for symbol in record.get("symbols", []):
                name = symbol.get("name")
                if name:
                    symbol_names.append(name)

        summaries.append({
            "kind": "typescript_folder_summary",
            "id": f"typescript_folder_summary:{safe_id(folder)}",
            "folder": folder,
            "file": folder,
            "files": file_paths[:50],
            "imports": imports[:50],
            "symbols": symbol_names[:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                (
                    f"TypeScript folder {folder}. "
                    f"Files: {', '.join(file_paths[:20]) or 'none'}. "
                    f"Symbols: {', '.join(symbol_names[:30]) or 'none'}. "
                    f"Imports {import_count}. Methods {method_count}."
                ),
                (
                    f"TypeScript folder {folder}. "
                    f"Files {len(file_paths)}. Symbols {compact_list(symbol_names, limit=6)}. "
                    f"Methods {method_count}."
                ),
            ),
            "summary": {
                "file_count": len(file_paths),
                "import_count": import_count,
                "symbol_count": len(symbol_names),
                "method_count": method_count,
            },
        })
    return summaries


def _python_module_area(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    if path.name == "__init__.py":
        return ".".join(path.parent.parts) or "root"
    return ".".join(path.parent.parts) or "root"


def _folder_area(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    return path.parent.as_posix() if path.parent.as_posix() not in {"", "."} else "root"

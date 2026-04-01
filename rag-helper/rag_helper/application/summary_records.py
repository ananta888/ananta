from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


def build_summary_records(index_records: list[dict], embedding_text_mode: str) -> tuple[list[dict], dict]:
    summary_records = [
        *build_java_package_summaries(index_records, embedding_text_mode),
        *build_java_module_summaries(index_records, embedding_text_mode),
        *build_python_module_summaries(index_records, embedding_text_mode),
        *build_typescript_folder_summaries(index_records, embedding_text_mode),
        *build_build_file_summaries(index_records, embedding_text_mode),
    ]
    return summary_records, {
        "summary_record_count": len(summary_records),
        "java_package_summary_count": sum(1 for record in summary_records if record["kind"] == "java_package_summary"),
        "java_module_summary_count": sum(1 for record in summary_records if record["kind"] == "java_module_summary"),
        "python_module_summary_count": sum(1 for record in summary_records if record["kind"] == "python_module_summary"),
        "typescript_folder_summary_count": sum(1 for record in summary_records if record["kind"] == "typescript_folder_summary"),
        "build_file_summary_count": sum(1 for record in summary_records if record["kind"] == "build_file_summary"),
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
            summary = _record_summary(record)
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


def build_java_module_summaries(index_records: list[dict], embedding_text_mode: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in index_records:
        if record.get("kind") not in {"java_file", "java_type"}:
            continue
        grouped[_module_area(record.get("file", ""))].append(record)

    summaries: list[dict] = []
    for module_name, records in sorted(grouped.items()):
        file_paths = sorted(record["file"] for record in records)
        packages: set[str] = set()
        type_names: list[str] = []
        imports_count = 0
        method_count = 0
        constructor_count = 0
        for record in records:
            package_name = record.get("package")
            if package_name:
                packages.add(package_name)
            imports_count += len(record.get("imports", []))
            summary = _record_summary(record)
            method_count += summary.get("method_count", 0)
            constructor_count += summary.get("constructor_count", 0)
            if record.get("kind") == "java_file":
                for type_info in record.get("types", []):
                    name = type_info.get("name")
                    if name:
                        type_names.append(name)
            else:
                type_name = record.get("name")
                if type_name:
                    type_names.append(type_name)

        summaries.append({
            "kind": "java_module_summary",
            "id": f"java_module_summary:{safe_id(module_name)}",
            "module": module_name,
            "file": module_name,
            "files": file_paths[:50],
            "packages": sorted(packages)[:30],
            "type_names": type_names[:50],
            "embedding_text": build_embedding_text(
                embedding_text_mode,
                (
                    f"Java module {module_name}. "
                    f"Files: {', '.join(file_paths[:20]) or 'none'}. "
                    f"Packages: {', '.join(sorted(packages)[:20]) or 'none'}. "
                    f"Types: {', '.join(type_names[:20]) or 'none'}. "
                    f"Methods {method_count}. Constructors {constructor_count}. Imports {imports_count}."
                ),
                (
                    f"Java module {module_name}. "
                    f"Packages {compact_list(sorted(packages), limit=6)}. "
                    f"Types {compact_list(type_names, limit=6)}. "
                    f"Methods {method_count}."
                ),
            ),
            "summary": {
                "file_count": len(file_paths),
                "package_count": len(packages),
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
            summary = _record_summary(record)
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
            summary = _record_summary(record)
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


def build_build_file_summaries(index_records: list[dict], embedding_text_mode: str) -> list[dict]:
    summaries: list[dict] = []
    for record in index_records:
        kind = record.get("kind")
        file_path = str(record.get("file") or "")
        if kind == "xml_file" and file_path.endswith("pom.xml"):
            summaries.append(_build_pom_summary(record, embedding_text_mode))
            continue
        if kind in {"properties_file", "yaml_file", "md_file"}:
            continue
        if kind not in {"typescript_file"} and not file_path.endswith(("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
            continue
        summary = _build_gradle_summary(record, embedding_text_mode)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _build_pom_summary(record: dict, embedding_text_mode: str) -> dict:
    summary = _record_summary(record)
    coordinates = summary.get("coordinates") or record.get("coordinates") or "unknown"
    dependencies = list(summary.get("dependencies", []) or record.get("dependencies", []) or [])
    file_path = str(record.get("file") or "")
    return {
        "kind": "build_file_summary",
        "id": f"build_file_summary:{safe_id(file_path)}",
        "file": file_path,
        "build_tool": "maven",
        "build_file_type": "pom",
        "module": _module_area(file_path),
        "coordinates": coordinates,
        "dependencies": dependencies[:40],
        "embedding_text": build_embedding_text(
            embedding_text_mode,
            f"Maven build file {file_path}. Coordinates {coordinates}. Dependencies {', '.join(dependencies[:20]) or 'none'}.",
            f"Maven build {file_path}. Coordinates {coordinates}. Dependencies {compact_list(dependencies, limit=6)}.",
        ),
        "summary": {
            "dependency_count": len(dependencies),
            "coordinates": coordinates,
        },
    }


def _build_gradle_summary(record: dict, embedding_text_mode: str) -> dict | None:
    file_path = str(record.get("file") or "")
    if not file_path.endswith(("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
        return None
    summary = _record_summary(record)
    keys = list(record.get("keys", []) or [])
    heading_count = summary.get("heading_count")
    gradle_kind = "settings" if "settings.gradle" in file_path else "build"
    return {
        "kind": "build_file_summary",
        "id": f"build_file_summary:{safe_id(file_path)}",
        "file": file_path,
        "build_tool": "gradle",
        "build_file_type": gradle_kind,
        "module": _module_area(file_path),
        "keys": keys[:40],
        "embedding_text": build_embedding_text(
            embedding_text_mode,
            f"Gradle {gradle_kind} file {file_path}. Keys {', '.join(keys[:20]) or 'none'}. Heading count {heading_count or 0}.",
            f"Gradle {gradle_kind} {file_path}. Keys {compact_list(keys, limit=6)}.",
        ),
        "summary": {
            "key_count": len(keys),
            "heading_count": heading_count or 0,
        },
    }


def _python_module_area(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    if path.name == "__init__.py":
        return ".".join(path.parent.parts) or "root"
    return ".".join(path.parent.parts) or "root"


def _folder_area(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    return path.parent.as_posix() if path.parent.as_posix() not in {"", "."} else "root"


def _module_area(rel_path: str) -> str:
    path = PurePosixPath(rel_path)
    if not path.parts:
        return "root"
    first = path.parts[0]
    if first == "src":
        return "root"
    return first or "root"


def _record_summary(record: dict) -> dict:
    summary = record.get("summary")
    return summary if isinstance(summary, dict) else {}

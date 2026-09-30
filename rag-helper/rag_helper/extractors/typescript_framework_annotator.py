"""Angular/React framework classification for TypeScript outline records."""

from __future__ import annotations

import re

REACT_COMPONENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
REACT_HOOK_NAME_PATTERN = re.compile(r"^use[A-Z][A-Za-z0-9_]*$")
REACT_HOOK_CALL_PATTERN = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*)\s*\(")
JSX_TAG_PATTERN = re.compile(r"<[A-Za-z][^>]*>")

ANGULAR_SELECTOR_PATTERN = re.compile(r"selector\s*:\s*['\"]([^'\"]+)['\"]")
ANGULAR_TEMPLATE_URL_PATTERN = re.compile(r"templateUrl\s*:\s*['\"]([^'\"]+)['\"]")
ANGULAR_STANDALONE_PATTERN = re.compile(r"standalone\s*:\s*(true|false)")
ANGULAR_IMPORTS_PATTERN = re.compile(r"imports\s*:\s*\[([^\]]*)\]")

ANGULAR_LIFECYCLE_METHODS = {
    "ngOnInit",
    "ngOnDestroy",
    "ngOnChanges",
    "ngDoCheck",
    "ngAfterViewInit",
    "ngAfterViewChecked",
    "ngAfterContentInit",
    "ngAfterContentChecked",
}

REACT_CLASS_LIFECYCLE_METHODS = {
    "componentDidMount",
    "componentDidUpdate",
    "componentWillUnmount",
    "shouldComponentUpdate",
    "getDerivedStateFromProps",
    "getSnapshotBeforeUpdate",
    "render",
}


class TypeScriptFrameworkAnnotator:
    """Annotate TypeScript symbol records with framework roles and metadata."""

    def annotate(
        self,
        *,
        rel_path: str,
        text: str,
        imports: list[str],
        top_level_symbols: list[dict],
        detail_records: list[dict],
    ) -> dict[str, object]:
        has_angular_import = any(module.startswith("@angular/") for module in imports)
        has_react_import = any(
            module in {"react", "react-dom", "react/jsx-runtime"}
            or module.startswith("react/")
            for module in imports
        )
        has_jsx = rel_path.endswith(".tsx") or bool(JSX_TAG_PATTERN.search(text))
        hook_calls = sorted({match.group(1) for match in REACT_HOOK_CALL_PATTERN.finditer(text)})
        frameworks: set[str] = set()
        framework_roles: list[str] = []
        frontend_artifacts: list[str] = []
        symbol_roles: dict[str, tuple[str | None, str | None]] = {}

        for record in top_level_symbols:
            framework, framework_role = self._classify_typescript_framework_symbol(
                record=record,
                rel_path=rel_path,
                has_angular_import=has_angular_import,
                has_react_import=has_react_import,
                has_jsx=has_jsx,
            )
            if framework is not None:
                record["framework"] = framework
                frameworks.add(framework)
            if framework_role is not None:
                record["framework_role"] = framework_role
                framework_roles.append(framework_role)
                frontend_artifacts.append(record["name"])
            if framework == "angular":
                angular_metadata = self._extract_angular_metadata(record.get("decorator_texts", []))
                if angular_metadata:
                    record["angular_metadata"] = angular_metadata
            if framework == "react":
                record["hook_calls"] = [call for call in hook_calls if call != record.get("name")]
                record["jsx_usage"] = has_jsx
            symbol_roles[str(record.get("id"))] = (framework, framework_role)

        for record in detail_records:
            parent_id = str(record.get("parent_id") or "")
            framework, framework_role = symbol_roles.get(parent_id, (None, None))
            if record.get("kind") in {"typescript_method", "typescript_constructor"} and framework is not None:
                record["framework"] = framework
                method_role = self._classify_typescript_method_role(
                    name=str(record.get("name") or ""),
                    framework=framework,
                    parent_role=framework_role,
                )
                if method_role is not None:
                    record["framework_role"] = method_role

        ordered_frameworks = sorted(frameworks)
        ordered_roles = list(dict.fromkeys(role for role in framework_roles if role))
        angular_artifact_count = sum(1 for record in top_level_symbols if record.get("framework") == "angular")
        react_artifact_count = sum(1 for record in top_level_symbols if record.get("framework") == "react")
        component_count = sum(
            1
            for record in top_level_symbols
            if record.get("framework_role")
            in {"angular_component", "react_component"}
        )
        hook_count = sum(1 for record in top_level_symbols if record.get("framework_role") == "react_hook")
        return {
            "frameworks": ordered_frameworks,
            "framework_roles": ordered_roles,
            "frontend_artifacts": frontend_artifacts,
            "hook_calls": hook_calls,
            "angular_artifact_count": angular_artifact_count,
            "react_artifact_count": react_artifact_count,
            "component_count": component_count,
            "hook_count": hook_count,
        }

    def _classify_typescript_framework_symbol(
        self,
        *,
        record: dict,
        rel_path: str,
        has_angular_import: bool,
        has_react_import: bool,
        has_jsx: bool,
    ) -> tuple[str | None, str | None]:
        decorators = set(record.get("decorators", []) or [])
        name = str(record.get("name") or "")
        kind = str(record.get("kind") or "")
        extends_value = str(record.get("extends") or "")

        angular_roles = {
            "@Component": "angular_component",
            "@Injectable": "angular_service",
            "@Directive": "angular_directive",
            "@Pipe": "angular_pipe",
            "@NgModule": "angular_module",
        }
        for decorator_name, framework_role in angular_roles.items():
            if decorator_name in decorators:
                return "angular", framework_role
        if (
            has_angular_import
            and kind == "typescript_class"
            and name.endswith(("Component", "Service", "Directive", "Pipe", "Module"))
        ):
            suffix_map = {
                "Component": "angular_component",
                "Service": "angular_service",
                "Directive": "angular_directive",
                "Pipe": "angular_pipe",
                "Module": "angular_module",
            }
            for suffix, framework_role in suffix_map.items():
                if name.endswith(suffix):
                    return "angular", framework_role

        if kind == "typescript_class" and (
            "React.Component" in extends_value
            or extends_value in {"Component", "PureComponent"}
        ):
            return "react", "react_component"

        if kind in {"typescript_function", "typescript_const"} and REACT_HOOK_NAME_PATTERN.match(name):
            if has_react_import or rel_path.endswith((".ts", ".tsx")):
                return "react", "react_hook"

        if kind in {"typescript_function", "typescript_const"} and REACT_COMPONENT_NAME_PATTERN.match(name):
            if has_jsx or has_react_import or rel_path.endswith(".tsx"):
                return "react", "react_component"

        return None, None

    def _classify_typescript_method_role(self, *, name: str, framework: str, parent_role: str | None) -> str | None:
        if framework == "angular" and name in ANGULAR_LIFECYCLE_METHODS:
            return "angular_lifecycle"
        if framework == "react" and name in REACT_CLASS_LIFECYCLE_METHODS:
            return "react_lifecycle"
        if framework == "react" and name == "render" and parent_role == "react_component":
            return "react_render"
        return None

    def _extract_angular_metadata(self, decorator_texts: list[str]) -> dict[str, object]:
        metadata: dict[str, object] = {}
        for decorator_text in decorator_texts:
            if not decorator_text.startswith("@Component"):
                continue
            selector_match = ANGULAR_SELECTOR_PATTERN.search(decorator_text)
            template_match = ANGULAR_TEMPLATE_URL_PATTERN.search(decorator_text)
            standalone_match = ANGULAR_STANDALONE_PATTERN.search(decorator_text)
            imports_match = ANGULAR_IMPORTS_PATTERN.search(decorator_text)
            if selector_match:
                metadata["selector"] = selector_match.group(1)
            if template_match:
                metadata["template_url"] = template_match.group(1)
            if standalone_match:
                metadata["standalone"] = standalone_match.group(1) == "true"
            if imports_match:
                metadata["imports"] = [
                    item.strip()
                    for item in imports_match.group(1).split(",")
                    if item.strip()
                ][:20]
        return metadata

"""Regex-only, non-executing outline extraction for shell languages."""

from __future__ import annotations

import re

from rag_helper.extractors.structured_support import StructuredRecordFactory, stats_for


class ShellScriptExtractor:
    SUPPORTED_EXTENSIONS = {"sh", "bash", "zsh", "fish"}

    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 5_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "shell", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        functions: list[str] = []
        variables: list[str] = []
        parameters: list[str] = []
        imports: list[str] = []
        local_calls: list[str] = []
        shell = self._shell_kind(rel_path, text)

        for line_no, raw in enumerate(text.splitlines(), start=1):
            code = self._strip_comment(raw).strip()
            if not code:
                continue
            function_match = re.match(
                r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_.:-]*)\s*(?:\(\s*\))?\s*\{?\s*$",
                code,
            )
            fish_match = re.match(r"^function\s+([A-Za-z_][A-Za-z0-9_.:-]*)\b", code)
            function_name = (
                fish_match.group(1)
                if fish_match
                else (
                    function_match.group(1)
                    if function_match and ("function" in code or "()" in code or code.endswith("{"))
                    else None
                )
            )
            if function_name:
                functions.append(function_name)
                details.append(
                    factory.symbol(
                        kind="shell_function",
                        name=function_name,
                        line=line_no,
                        ordinal=len(functions),
                        shell=shell,
                        confidence=0.65,
                    )
                )

            assignment = re.match(
                r"^(?:export\s+|readonly\s+|local\s+|declare\s+(?:-[A-Za-z]+\s+)?)?([A-Za-z_][A-Za-z0-9_]*)=", code
            )
            if assignment:
                name = assignment.group(1)
                if name not in variables:
                    variables.append(name)
                    details.append(
                        factory.symbol(
                            kind="shell_variable",
                            name=name,
                            line=line_no,
                            column=raw.find(name) + 1,
                            ordinal=len(variables),
                            value_redacted=True,
                            confidence=0.65,
                        )
                    )

            for parameter_match in re.finditer(r"\$(?:\{)?([1-9][0-9]*|[@*#?_-])(?:\})?", code):
                parameter = parameter_match.group(1)
                if parameter not in parameters:
                    parameters.append(parameter)
                    details.append(
                        factory.symbol(
                            kind="shell_parameter",
                            name=parameter,
                            line=line_no,
                            column=parameter_match.start() + 1,
                            ordinal=len(parameters),
                            confidence=0.6,
                        )
                    )

            source_match = re.match(r"^(?:source|\.)\s+(['\"]?)([^\s'\"]+)\1", code)
            if source_match:
                target = source_match.group(2)
                imports.append(target)
                record = factory.symbol(
                    kind="shell_source",
                    name=target,
                    line=line_no,
                    ordinal=len(imports),
                    confidence=0.65,
                )
                details.append(record)
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="shell_file",
                        source_name=rel_path,
                        relation="sources_script",
                        target=target,
                        line=line_no,
                        confidence=0.65,
                    )
                )

            for call_match in re.finditer(r"(?:^|[;&|]\s*)(\.{0,2}/[^\s;&|]+|[^\s;&|]+\.(?:sh|bash|zsh|fish))\b", code):
                target = call_match.group(1).strip("'\"")
                if target in imports:
                    continue
                local_calls.append(target)
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="shell_file",
                        source_name=rel_path,
                        relation="calls_local_script",
                        target=target,
                        line=line_no,
                        confidence=0.55,
                    )
                )

            if re.search(r"(?:^|[;&|]\s*)eval(?:\s|$)", code):
                diagnostic = factory.diagnostic(
                    "shell_dynamic_eval",
                    "Dynamic eval prevents reliable static command resolution.",
                    line=line_no,
                    severity="security",
                    fallback="regex_fallback",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)

            if len(details) + len(relations) >= self.max_records:
                diagnostic = factory.diagnostic(
                    "shell_record_limit_reached",
                    f"Script extraction stopped at {self.max_records} records.",
                    line=line_no,
                    fallback="partial_regex_index",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
                break

        index = [
            factory.file_record(
                summary={
                    "shell": shell,
                    "function_count": len(functions),
                    "parameter_count": len(parameters),
                    "variable_count": len(variables),
                    "import_count": len(imports),
                    "local_call_count": len(local_calls),
                    "diagnostic_count": len(diagnostics),
                },
                labels=functions + imports + local_calls,
                parser_mode="regex_fallback",
                confidence=0.55,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "shell",
                rel_path,
                index,
                details,
                relations,
                parser_mode="regex_fallback",
                diagnostics=diagnostics,
                confidence=0.55,
                shell=shell,
                function_count=len(functions),
                parameter_count=len(parameters),
                variable_count=len(variables),
                import_count=len(imports),
                local_call_count=len(local_calls),
            ),
        )

    @staticmethod
    def _strip_comment(line: str) -> str:
        # This is intentionally conservative: a hash following whitespace is
        # treated as comment, while parameter expansion such as ${x#prefix}
        # remains intact.
        match = re.search(r"(?:^|\s)#", line)
        return line[: match.start()] if match else line

    @staticmethod
    def _shell_kind(rel_path: str, text: str) -> str:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        for candidate in ("bash", "zsh", "fish", "sh"):
            if first_line.startswith("#!") and re.search(rf"(?:/|\s){candidate}(?:\s|$)", first_line):
                return candidate
        ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path.rsplit("/", 1)[-1] else ""
        return ext if ext in ShellScriptExtractor.SUPPORTED_EXTENSIONS else "shell"


class PowerShellExtractor:
    SUPPORTED_EXTENSIONS = {"ps1", "psm1"}

    def __init__(self, embedding_text_mode: str = "verbose", max_records: int = 5_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "powershell", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        functions: list[str] = []
        parameters: list[str] = []
        variables: list[str] = []
        imports: list[str] = []
        local_calls: list[str] = []
        in_block_comment = False
        in_param_block = False
        param_parenthesis_depth = 0

        for line_no, raw in enumerate(text.splitlines(), start=1):
            code, in_block_comment = self._strip_comments(raw, in_block_comment)
            if not code.strip():
                continue
            if re.search(r"\bparam\s*\(", code, re.IGNORECASE):
                in_param_block = True
                param_parenthesis_depth = code.count("(") - code.count(")")
            function_match = re.match(r"^\s*(?:filter|function)\s+([A-Za-z_][\w-]*)", code, re.IGNORECASE)
            if function_match:
                name = function_match.group(1)
                functions.append(name)
                details.append(
                    factory.symbol(
                        kind="powershell_function",
                        name=name,
                        line=line_no,
                        column=function_match.start(1) + 1,
                        ordinal=len(functions),
                        confidence=0.65,
                    )
                )

            for match in re.finditer(r"\$([A-Za-z_][\w:]*)", code):
                name = match.group(1)
                if name.lower().startswith(("env:", "global:", "script:")):
                    normalized = name
                else:
                    normalized = name
                if re.search(rf"\${re.escape(name)}\s*=", code, re.IGNORECASE) and normalized not in variables:
                    variables.append(normalized)
                    details.append(
                        factory.symbol(
                            kind="powershell_variable",
                            name=normalized,
                            line=line_no,
                            column=match.start(1) + 1,
                            ordinal=len(variables),
                            value_redacted=True,
                            confidence=0.65,
                        )
                    )
                if (
                    in_param_block or re.search(r"\[(?:Parameter|Alias)", code, re.IGNORECASE)
                ) and normalized not in parameters:
                    parameters.append(normalized)
                    details.append(
                        factory.symbol(
                            kind="powershell_parameter",
                            name=normalized,
                            line=line_no,
                            column=match.start(1) + 1,
                            ordinal=len(parameters),
                            confidence=0.55,
                        )
                    )

            if in_param_block:
                if not re.search(r"\bparam\s*\(", code, re.IGNORECASE):
                    param_parenthesis_depth += code.count("(") - code.count(")")
                if param_parenthesis_depth <= 0:
                    in_param_block = False

            import_match = re.search(r"\bImport-Module\s+(?:-Name\s+)?['\"]?([^\s'\"]+)", code, re.IGNORECASE)
            dot_source_match = re.match(r"^\s*\.\s+(?:['\"]([^'\"]+)['\"]|([^\s;]+))", code)
            if import_match or dot_source_match:
                target = (
                    import_match.group(1) if import_match else dot_source_match.group(1) or dot_source_match.group(2)
                )
                imports.append(target)
                relation_type = "imports_module" if import_match else "sources_script"
                details.append(
                    factory.symbol(
                        kind="powershell_import",
                        name=target,
                        line=line_no,
                        ordinal=len(imports),
                        import_kind=relation_type,
                        confidence=0.65,
                    )
                )
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="powershell_file",
                        source_name=rel_path,
                        relation=relation_type,
                        target=target,
                        line=line_no,
                        confidence=0.65,
                    )
                )

            for match in re.finditer(
                r"(?:^|[;&|]\s*)(&\s*)?['\"]?(\.{0,2}[/\\][^\s;'\"]+\.ps1)\b", code, re.IGNORECASE
            ):
                target = match.group(2)
                local_calls.append(target)
                relations.append(
                    factory.relation(
                        source_id=factory.file_id,
                        source_kind="powershell_file",
                        source_name=rel_path,
                        relation="calls_local_script",
                        target=target,
                        line=line_no,
                        confidence=0.55,
                    )
                )

            if re.search(r"\b(?:Invoke-Expression|iex)\b", code, re.IGNORECASE):
                diagnostic = factory.diagnostic(
                    "powershell_dynamic_expression",
                    "Invoke-Expression prevents reliable static command resolution.",
                    line=line_no,
                    severity="security",
                    fallback="regex_fallback",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)

            if len(details) + len(relations) >= self.max_records:
                diagnostic = factory.diagnostic(
                    "powershell_record_limit_reached",
                    f"Script extraction stopped at {self.max_records} records.",
                    line=line_no,
                    fallback="partial_regex_index",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
                break

        index = [
            factory.file_record(
                summary={
                    "function_count": len(functions),
                    "parameter_count": len(parameters),
                    "variable_count": len(variables),
                    "import_count": len(imports),
                    "local_call_count": len(local_calls),
                    "diagnostic_count": len(diagnostics),
                },
                labels=functions + imports + local_calls,
                parser_mode="regex_fallback",
                confidence=0.55,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "powershell",
                rel_path,
                index,
                details,
                relations,
                parser_mode="regex_fallback",
                diagnostics=diagnostics,
                confidence=0.55,
                function_count=len(functions),
                parameter_count=len(parameters),
                variable_count=len(variables),
                import_count=len(imports),
                local_call_count=len(local_calls),
            ),
        )

    @staticmethod
    def _strip_comments(line: str, in_block: bool) -> tuple[str, bool]:
        result = line
        if in_block:
            if "#>" not in result:
                return "", True
            result = result.split("#>", 1)[1]
            in_block = False
        while "<#" in result:
            before, after = result.split("<#", 1)
            if "#>" in after:
                result = before + after.split("#>", 1)[1]
            else:
                result = before
                in_block = True
                break
        comment = re.search(r"(?:^|\s)#", result)
        if comment:
            result = result[: comment.start()]
        return result, in_block

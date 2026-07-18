"""Static extraction for container, CI and build orchestration files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Iterable

import yaml

from rag_helper.extractors.base import FileSkipped
from rag_helper.extractors.configuration_extractor import ConfigurationExtractor
from rag_helper.extractors.structured_support import StructuredRecordFactory, is_secret_key, stats_for


class DockerfileExtractor:
    def __init__(self, embedding_text_mode: str = "verbose", max_instructions: int = 5_000) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_instructions = max_instructions

    def parse(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "dockerfile", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        diagnostics: list[dict] = []
        stages: list[str] = []
        base_images: list[str] = []
        env_keys: list[str] = []
        ports: list[str] = []
        copy_count = 0
        entrypoint_count = 0
        current_stage_id = factory.file_id

        for ordinal, (line, instruction, argument) in enumerate(self._instructions(text), start=1):
            if ordinal > self.max_instructions:
                diagnostic = factory.diagnostic(
                    "dockerfile_instruction_limit_reached",
                    f"Only the first {self.max_instructions} instructions were indexed.",
                    line=line,
                    fallback="partial_structured_index",
                )
                diagnostics.append(diagnostic)
                details.append(diagnostic)
                break
            upper = instruction.upper()
            if upper == "FROM":
                match = re.match(r"(?:--platform=\S+\s+)?([^\s]+)(?:\s+AS\s+([^\s]+))?", argument, re.IGNORECASE)
                if not match:
                    diagnostics.append(
                        factory.diagnostic(
                            "dockerfile_invalid_from",
                            "FROM instruction could not be parsed.",
                            line=line,
                            fallback="instruction_index",
                        )
                    )
                    continue
                image = match.group(1)
                stage = match.group(2) or f"stage_{len(stages)}"
                base_images.append(image)
                stages.append(stage)
                record = factory.symbol(
                    kind="docker_stage",
                    name=stage,
                    line=line,
                    ordinal=len(stages),
                    base_image=image,
                )
                current_stage_id = record["id"]
                details.append(record)
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind="docker_stage",
                        source_name=stage,
                        relation="uses_base_image",
                        target=image,
                        line=line,
                    )
                )
            elif upper in {"COPY", "ADD"}:
                copy_count += 1
                sources, destination = self._copy_parts(argument)
                record = factory.symbol(
                    kind="docker_copy",
                    name=f"{upper}_{copy_count}",
                    line=line,
                    parent_id=current_stage_id,
                    ordinal=copy_count,
                    instruction=upper,
                    sources=sources,
                    destination=destination,
                )
                details.append(record)
                for source in sources:
                    if source.startswith(("http://", "https://")):
                        relation_type = "fetches_remote_source"
                    elif source.startswith("--from="):
                        relation_type = "copies_from_stage"
                    else:
                        relation_type = "copies_local_path"
                    relations.append(
                        factory.relation(
                            source_id=current_stage_id,
                            source_kind="docker_stage",
                            source_name=stages[-1] if stages else rel_path,
                            relation=relation_type,
                            target=source,
                            line=line,
                        )
                    )
            elif upper == "ENV":
                for key in self._env_keys(argument):
                    env_keys.append(key)
                    details.append(
                        factory.symbol(
                            kind="docker_env_key",
                            name=key,
                            line=line,
                            parent_id=current_stage_id,
                            ordinal=len(env_keys),
                            value_redacted=True,
                            suspected_secret=is_secret_key(key),
                        )
                    )
            elif upper == "EXPOSE":
                for port in argument.split():
                    ports.append(port)
                    details.append(
                        factory.symbol(
                            kind="docker_port",
                            name=port,
                            line=line,
                            parent_id=current_stage_id,
                            ordinal=len(ports),
                        )
                    )
            elif upper in {"ENTRYPOINT", "CMD"}:
                entrypoint_count += 1
                details.append(
                    factory.symbol(
                        kind="docker_entrypoint",
                        name=upper.lower(),
                        line=line,
                        parent_id=current_stage_id,
                        ordinal=entrypoint_count,
                        form="json" if argument.lstrip().startswith("[") else "shell",
                        executed=False,
                    )
                )

        details.extend(item for item in diagnostics if item not in details)
        index = [
            factory.file_record(
                summary={
                    "stage_count": len(stages),
                    "base_image_count": len(base_images),
                    "copy_count": copy_count,
                    "env_key_count": len(env_keys),
                    "port_count": len(ports),
                    "entrypoint_count": entrypoint_count,
                    "diagnostic_count": len(diagnostics),
                },
                labels=stages + base_images + ports,
                parser_mode="instruction_lexer",
                confidence=0.9,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "dockerfile",
                rel_path,
                index,
                details,
                relations,
                parser_mode="instruction_lexer",
                diagnostics=diagnostics,
                stage_count=len(stages),
                base_image_count=len(base_images),
                copy_count=copy_count,
                env_key_count=len(env_keys),
                port_count=len(ports),
            ),
        )

    @staticmethod
    def _instructions(text: str) -> Iterable[tuple[int, str, str]]:
        buffer = ""
        start = 1
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not buffer and (not stripped or stripped.startswith("#")):
                continue
            if not buffer:
                start = line_no
            buffer += (" " if buffer else "") + raw.rstrip().rstrip("\\").strip()
            if raw.rstrip().endswith("\\"):
                continue
            match = re.match(r"^([A-Za-z]+)\s+(.*)$", buffer, re.DOTALL)
            if match:
                yield start, match.group(1), match.group(2).strip()
            buffer = ""
        if buffer:
            match = re.match(r"^([A-Za-z]+)\s+(.*)$", buffer, re.DOTALL)
            if match:
                yield start, match.group(1), match.group(2).strip()

    @staticmethod
    def _copy_parts(argument: str) -> tuple[list[str], str | None]:
        stage_options = re.findall(r"--(?:from|chown|chmod|exclude)=\S+", argument)
        payload = re.sub(r"^(?:--\S+=\S+\s+)+", "", argument).strip()
        if payload.startswith("["):
            values = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', payload)
        else:
            values = payload.split()
        if len(values) < 2:
            return stage_options + values, None
        return stage_options + values[:-1], values[-1]

    @staticmethod
    def _env_keys(argument: str) -> list[str]:
        assignment_keys = re.findall(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=", argument)
        if assignment_keys:
            return assignment_keys
        first = argument.split(None, 1)
        return first[:1] if first else []


class YamlInfrastructureExtractor:
    """Route YAML to Compose, GitHub Actions or the generic safe extractor."""

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_aliases: int = 50,
        max_nodes: int = 20_000,
        max_depth: int = 64,
        max_records: int = 5_000,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.config_extractor = ConfigurationExtractor(
            embedding_text_mode=embedding_text_mode,
            max_aliases=max_aliases,
            max_nodes=max_nodes,
            max_depth=max_depth,
            max_records=max_records,
        )

    def parse(self, rel_path: str, text: str):
        normalized = rel_path.replace("\\", "/").lower()
        basename = PurePosixPath(normalized).name
        if basename.startswith(("docker-compose", "compose")):
            return self._parse_compose(rel_path, text)
        if "/.github/workflows/" in f"/{normalized}" or normalized.startswith(".github/workflows/"):
            return self._parse_github_actions(rel_path, text)

        try:
            self.config_extractor.validate_yaml(text)
            parsed = yaml.safe_load(text)
        except Exception:
            return self.config_extractor.parse(rel_path, text)
        if isinstance(parsed, dict) and isinstance(parsed.get("services"), dict):
            return self._parse_compose(rel_path, text, parsed=parsed)
        if isinstance(parsed, dict) and isinstance(parsed.get("jobs"), dict) and ("on" in parsed or True in parsed):
            return self._parse_github_actions(rel_path, text, parsed=parsed)
        return self.config_extractor.parse(rel_path, text)

    def _parse_compose(self, rel_path: str, text: str, parsed: object | None = None):
        factory = StructuredRecordFactory(rel_path, "compose", self.embedding_text_mode)
        diagnostics: list[dict] = []
        if parsed is None:
            try:
                self.config_extractor.validate_yaml(text)
                parsed = yaml.safe_load(text)
            except Exception:
                diagnostic = factory.diagnostic(
                    "compose_parse_error",
                    "Compose YAML could not be parsed safely.",
                    severity="error",
                    fallback="text_index",
                )
                index = [
                    factory.file_record(
                        summary={"service_count": 0, "diagnostic_count": 1}, parser_mode="text_index", confidence=0.25
                    )
                ]
                return (
                    index,
                    [diagnostic],
                    [],
                    stats_for(
                        "compose",
                        rel_path,
                        index,
                        [diagnostic],
                        [],
                        parser_mode="text_index",
                        diagnostics=[diagnostic],
                        service_count=0,
                    ),
                )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
            return self.config_extractor.parse(rel_path, text)

        line_map = self._yaml_line_map(text)
        details: list[dict] = []
        relations: list[dict] = []
        services: dict = parsed["services"]
        service_ids: dict[str, str] = {}
        network_names: set[str] = set()
        volume_names: set[str] = set()
        for ordinal, (name, raw_service) in enumerate(services.items(), start=1):
            service = raw_service if isinstance(raw_service, dict) else {}
            line = line_map.get(f"services.{name}", 1)
            environment_keys = self._environment_keys(service.get("environment"))
            networks = self._name_list(service.get("networks"))
            volumes = [self._volume_name(item) for item in self._name_list(service.get("volumes"))]
            volumes = [item for item in volumes if item]
            dependencies = self._name_list(service.get("depends_on"))
            image = service.get("image") if isinstance(service.get("image"), str) else None
            build_path, dockerfile = self._build_info(service.get("build"))
            record = factory.symbol(
                kind="compose_service",
                name=str(name),
                line=line,
                ordinal=ordinal,
                image=image,
                build_path=build_path,
                dockerfile=dockerfile,
                environment_keys=environment_keys,
                environment_values_redacted=True,
                networks=networks,
                volumes=volumes,
                depends_on=dependencies,
                has_healthcheck=isinstance(service.get("healthcheck"), dict),
            )
            details.append(record)
            service_ids[str(name)] = record["id"]
            network_names.update(networks)
            volume_names.update(volumes)
            for relation_type, targets in (
                ("uses_image", [image] if image else []),
                ("builds_from_context", [build_path] if build_path else []),
                ("uses_dockerfile", [dockerfile] if dockerfile else []),
                ("depends_on_service", dependencies),
                ("uses_network", networks),
                ("mounts_volume", volumes),
            ):
                for target in targets:
                    relations.append(
                        factory.relation(
                            source_id=record["id"],
                            source_kind="compose_service",
                            source_name=str(name),
                            relation=relation_type,
                            target=str(target),
                            line=line,
                            build_context=build_path if relation_type == "uses_dockerfile" else None,
                        )
                    )

        for relation in relations:
            if relation["relation"] == "depends_on_service" and relation["target"] in service_ids:
                relation["target_resolved"] = service_ids[relation["target"]]
                relation["resolution_status"] = "resolved"

        index = [
            factory.file_record(
                summary={
                    "service_count": len(services),
                    "network_count": len(network_names),
                    "volume_count": len(volume_names),
                    "diagnostic_count": len(diagnostics),
                },
                labels=list(services) + sorted(network_names) + sorted(volume_names),
                parser_mode="safe_yaml",
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "compose",
                rel_path,
                index,
                details,
                relations,
                parser_mode="safe_yaml",
                diagnostics=diagnostics,
                service_count=len(services),
                network_count=len(network_names),
                volume_count=len(volume_names),
            ),
        )

    def _parse_github_actions(self, rel_path: str, text: str, parsed: object | None = None):
        factory = StructuredRecordFactory(rel_path, "github_actions", self.embedding_text_mode)
        if parsed is None:
            try:
                self.config_extractor.validate_yaml(text)
                parsed = yaml.safe_load(text)
            except Exception:
                diagnostic = factory.diagnostic(
                    "github_actions_parse_error",
                    "Workflow YAML could not be parsed safely.",
                    severity="error",
                    fallback="text_index",
                )
                index = [
                    factory.file_record(
                        summary={"job_count": 0, "diagnostic_count": 1}, parser_mode="text_index", confidence=0.25
                    )
                ]
                return (
                    index,
                    [diagnostic],
                    [],
                    stats_for(
                        "github_actions",
                        rel_path,
                        index,
                        [diagnostic],
                        [],
                        parser_mode="text_index",
                        diagnostics=[diagnostic],
                        job_count=0,
                    ),
                )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("jobs"), dict):
            return self.config_extractor.parse(rel_path, text)

        jobs: dict = parsed["jobs"]
        on_value = parsed.get("on", parsed.get(True, []))
        triggers = self._name_list(on_value)
        line_map = self._yaml_line_map(text)
        details: list[dict] = []
        relations: list[dict] = []
        job_ids: dict[str, str] = {}
        uses_values: list[str] = []
        for ordinal, (job_name, raw_job) in enumerate(jobs.items(), start=1):
            job = raw_job if isinstance(raw_job, dict) else {}
            line = line_map.get(f"jobs.{job_name}", 1)
            needs = self._name_list(job.get("needs"))
            record = factory.symbol(
                kind="github_actions_job",
                name=str(job_name),
                line=line,
                ordinal=ordinal,
                needs=needs,
                runner=str(job.get("runs-on")) if job.get("runs-on") is not None else None,
            )
            details.append(record)
            job_ids[str(job_name)] = record["id"]
            for target in needs:
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind="github_actions_job",
                        source_name=str(job_name),
                        relation="needs_job",
                        target=target,
                        line=line,
                    )
                )
            steps = job.get("steps") if isinstance(job.get("steps"), list) else []
            for step_ordinal, raw_step in enumerate(steps, start=1):
                step = raw_step if isinstance(raw_step, dict) else {}
                step_name = str(step.get("name") or step.get("id") or f"step_{step_ordinal}")
                step_line = line_map.get(f"jobs.{job_name}.steps[{step_ordinal - 1}]", line)
                uses = step.get("uses") if isinstance(step.get("uses"), str) else None
                if uses:
                    uses_values.append(uses)
                step_record = factory.symbol(
                    kind="github_actions_step",
                    name=step_name,
                    line=step_line,
                    parent_id=record["id"],
                    ordinal=step_ordinal,
                    uses=uses,
                    has_run_command="run" in step,
                    executed=False,
                )
                details.append(step_record)
                if uses:
                    relations.append(
                        factory.relation(
                            source_id=step_record["id"],
                            source_kind="github_actions_step",
                            source_name=step_name,
                            relation="uses_action",
                            target=uses,
                            line=step_line,
                        )
                    )

        for relation in relations:
            if relation["relation"] == "needs_job" and relation["target"] in job_ids:
                relation["target_resolved"] = job_ids[relation["target"]]
                relation["resolution_status"] = "resolved"

        secret_names = sorted(set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE)))
        for ordinal, secret_name in enumerate(secret_names, start=1):
            match = re.search(rf"secrets\.{re.escape(secret_name)}\b", text, re.IGNORECASE)
            line = text.count("\n", 0, match.start()) + 1 if match else 1
            details.append(
                factory.symbol(
                    kind="github_actions_secret_reference",
                    name=secret_name,
                    line=line,
                    ordinal=ordinal,
                    value_redacted=True,
                )
            )

        index = [
            factory.file_record(
                summary={
                    "job_count": len(jobs),
                    "step_count": sum(1 for item in details if item.get("kind") == "github_actions_step"),
                    "action_count": len(uses_values),
                    "trigger_count": len(triggers),
                    "secret_reference_count": len(secret_names),
                },
                labels=list(jobs) + triggers + uses_values + secret_names,
                parser_mode="safe_yaml",
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "github_actions",
                rel_path,
                index,
                details,
                relations,
                parser_mode="safe_yaml",
                job_count=len(jobs),
                step_count=sum(1 for item in details if item.get("kind") == "github_actions_step"),
                action_count=len(uses_values),
                trigger_count=len(triggers),
                secret_reference_count=len(secret_names),
            ),
        )

    def _yaml_line_map(self, text: str) -> dict[str, int]:
        entries, _ = self.config_extractor.validate_yaml(text)
        return {entry.path: entry.line for entry in entries}

    @staticmethod
    def _name_list(value: object) -> list[str]:
        if isinstance(value, dict):
            return [str(item) for item in value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]

    @staticmethod
    def _environment_keys(value: object) -> list[str]:
        if isinstance(value, dict):
            return [str(item) for item in value]
        if isinstance(value, list):
            return [str(item).split("=", 1)[0] for item in value]
        return []

    @staticmethod
    def _volume_name(value: str) -> str | None:
        if not value or value.startswith((".", "/", "~", "${")):
            return None
        return value.split(":", 1)[0]

    @staticmethod
    def _build_info(value: object) -> tuple[str | None, str | None]:
        if isinstance(value, str):
            return value, None
        if isinstance(value, dict):
            context = value.get("context") if isinstance(value.get("context"), str) else None
            dockerfile = value.get("dockerfile") if isinstance(value.get("dockerfile"), str) else None
            return context, dockerfile
        return None, None


class BuildScriptExtractor:
    """Dispatch extensionless well-known build files without executing them."""

    def __init__(
        self,
        embedding_text_mode: str = "verbose",
        max_records: int = 10_000,
        max_docker_instructions: int = 5_000,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.max_records = max_records
        self.max_docker_instructions = max_docker_instructions

    def parse(self, rel_path: str, text: str):
        basename = PurePosixPath(rel_path.replace("\\", "/")).name.lower()
        if basename in {"dockerfile", "containerfile"} or basename.startswith(("dockerfile.", "containerfile.")):
            return DockerfileExtractor(
                self.embedding_text_mode,
                max_instructions=self.max_docker_instructions,
            ).parse(rel_path, text)
        if basename in {"makefile", "gnumakefile"} or basename.endswith(".mk"):
            return self._parse_makefile(rel_path, text)
        if basename == "jenkinsfile" or basename.startswith("jenkinsfile."):
            return self._parse_jenkinsfile(rel_path, text)
        if text.startswith("#!"):
            from rag_helper.extractors.script_extractor import ShellScriptExtractor

            return ShellScriptExtractor(self.embedding_text_mode).parse(rel_path, text)
        raise FileSkipped("unsupported_extensionless_file", {"basename": basename})

    def _parse_makefile(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "makefile", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        targets: dict[str, str] = {}
        pending_dependencies: list[tuple[dict, list[str]]] = []
        for ordinal, (line_no, raw) in enumerate(enumerate(text.splitlines(), start=1), start=1):
            if raw.startswith(("\t", " ")) or raw.lstrip().startswith(("#", ".PHONY")):
                continue
            match = re.match(r"^([^:=\s][^:=]*?)\s*:(?![=])\s*(.*)$", raw)
            if not match:
                continue
            names = [item for item in match.group(1).split() if not item.startswith(".")]
            dependencies = [item for item in match.group(2).split() if not item.startswith(("|", "$"))]
            for name in names:
                record = factory.symbol(
                    kind="make_target",
                    name=name,
                    line=line_no,
                    ordinal=ordinal,
                    dependencies=dependencies,
                    recipes_executed=False,
                )
                details.append(record)
                targets[name] = record["id"]
                pending_dependencies.append((record, dependencies))
        for record, dependencies in pending_dependencies:
            for target in dependencies:
                relations.append(
                    factory.relation(
                        source_id=record["id"],
                        source_kind="make_target",
                        source_name=record["name"],
                        relation="depends_on_target",
                        target=target,
                        target_resolved=targets.get(target),
                        line=record["line"],
                    )
                )
        index = [
            factory.file_record(
                summary={"target_count": len(details)},
                labels=list(targets),
                parser_mode="declaration_regex",
                confidence=0.8,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "makefile",
                rel_path,
                index,
                details,
                relations,
                parser_mode="declaration_regex",
                target_count=len(details),
            ),
        )

    def _parse_jenkinsfile(self, rel_path: str, text: str):
        factory = StructuredRecordFactory(rel_path, "jenkinsfile", self.embedding_text_mode)
        details: list[dict] = []
        relations: list[dict] = []
        for ordinal, match in enumerate(re.finditer(r"\bstage\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", text), start=1):
            line = text.count("\n", 0, match.start()) + 1
            record = factory.symbol(
                kind="jenkins_stage",
                name=match.group(1),
                line=line,
                column=match.start(1) - text.rfind("\n", 0, match.start(1)),
                ordinal=ordinal,
                steps_executed=False,
            )
            details.append(record)
            relations.append(
                factory.relation(
                    source_id=factory.file_id,
                    source_kind="jenkinsfile_file",
                    source_name=rel_path,
                    relation="contains_stage",
                    target=record["name"],
                    target_resolved=record["id"],
                    line=line,
                )
            )
        index = [
            factory.file_record(
                summary={"stage_count": len(details)},
                labels=[item["name"] for item in details],
                parser_mode="declaration_regex",
                confidence=0.75,
            )
        ]
        return (
            index,
            details,
            relations,
            stats_for(
                "jenkinsfile",
                rel_path,
                index,
                details,
                relations,
                parser_mode="declaration_regex",
                stage_count=len(details),
            ),
        )

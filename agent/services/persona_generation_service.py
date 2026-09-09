"""Hub generation Task/run lifecycle; rendering stays in the delegated Worker."""

import base64
import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generated_source import PersonaGeneratedOutput, PersonaGenerationRunPin, receipt_digest
from agent.models.persona_generation import admission_digest
from ananta_contracts.persona_generation import MAX_OUTPUT, decode_result, recipe_digest, validate_assignment


class PersonaGeneratorPort(Protocol):
    def execute(self, assignment, recipe) -> bytes: ...


@dataclass(frozen=True)
class GeneratedPersona:
    source: PersonaSourcePin
    output: PersonaGeneratedOutput
    run_pin: PersonaGenerationRunPin
    content: bytes = field(repr=False)


class HubPersonaGenerationService:
    def __init__(
        self,
        *,
        policy,
        registry,
        state,
        leases,
        worker: PersonaGeneratorPort,
        admission,
        repository_revision,
        execution_profile_digest,
        environment_digest,
        clock=time.time,
    ):
        if type(repository_revision) is not str or not re.fullmatch(
            r"(?:[a-f0-9]{40}|[a-f0-9]{64})", repository_revision
        ):
            raise ValueError("persona_generation_repository_binding_required")
        if any(
            type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in (execution_profile_digest, environment_digest)
        ):
            raise ValueError("persona_generation_runtime_binding_required")
        self.policy, self.registry, self.state, self.leases = policy, registry, state, leases
        self.worker, self.admission, self.clock = worker, admission, clock
        self.revision, self.profile_digest, self.environment_digest = (
            repository_revision,
            execution_profile_digest,
            environment_digest,
        )

    def _reserve(self, principal, project, request):
        recipe = request.recipe.model_dump(mode="json")
        test = request.classification == "test_only"
        source = self.registry.register_source(
            tenant_id=principal.tenant_id,
            project_id=project,
            origin_type="persona_generation_recipe",
            origin_digest=receipt_digest({"recipe": recipe, "repository_revision": self.revision}),
            content_digest=recipe_digest(recipe),
            policy_digest=request.digest(),
            evidence_scope="test" if test else "local",
            synthetic=test,
        )
        source_pin = PersonaSourcePin(source_id=source.source_id, binding_digest=source.binding_digest)
        task, assignment, lease = (str(uuid.uuid4()) for _ in range(3))
        run = self.registry.reserve_run(
            tenant_id=principal.tenant_id,
            project_id=project,
            task_id=task,
            assignment_id=assignment,
            dispatch_lease_id=lease,
            repository_revision=self.revision,
            input_digest=source.content_digest,
            execution_profile_digest=self.profile_digest,
            environment_digest=self.environment_digest,
            source_ids=(source.source_id, request.license.source_id),
            evidence_scope="test" if test else "local",
            synthetic=test,
            idempotency_key=f"persona-generation-{task}",
        )
        return source_pin, run

    def generate(self, principal, project, request):
        request = self.policy.require(principal, project, request)
        source, run = self._reserve(principal, project, request)
        assignment = {
            "schema": "ananta.persona-generation-task.v1",
            "task_id": run.task_id,
            "assignment_id": run.assignment_id,
            "lease_id": run.dispatch_lease_id,
            "tenant_id": principal.tenant_id,
            "project_id": project,
            "owner_subject": principal.subject_id,
            "run_id": run.run_id,
            "run_binding_digest": run.binding_digest,
            "source_sha256": run.input_digest,
            "admission_digest": admission_digest(request, source),
            "deadline": int(self.clock()) + 20,
        }
        recorded = False
        try:
            assignment["evidence"] = self.registry.assignment_projection(
                tenant_id=principal.tenant_id,
                project_id=project,
                run_id=run.run_id,
                task_id=run.task_id,
                assignment_id=run.assignment_id,
                dispatch_lease_id=run.dispatch_lease_id,
            )
            validate_assignment(assignment, self.clock())
            self.state.start(assignment, request, source=source)
            self.leases.require(assignment)
            recipe = request.recipe.model_dump(mode="json")
            content = self.worker.execute(assignment, recipe)
            if type(content) is not bytes or not 16 <= len(content) <= MAX_OUTPUT:
                raise ValueError("persona_generation_worker_content_invalid")
            mime = "image/png" if recipe["media_kind"] == "image" else "video/mp4"
            decode_result(
                {
                    "task_id": run.task_id,
                    "lease_id": run.dispatch_lease_id,
                    "media_type": mime,
                    "content": base64.b64encode(content).decode(),
                },
                assignment,
                recipe,
            )
            self.leases.require(assignment)
            output = PersonaGeneratedOutput(
                schema_version="ananta.persona-generated-output.v1",
                tenant_id=principal.tenant_id,
                project_id=project,
                owner_subject=principal.subject_id,
                media_kind=recipe["media_kind"],
                media_type=mime,
                content_sha256=hashlib.sha256(content).hexdigest(),
                content_size=len(content),
                inputs=(source,),
                license=request.license,
                consent=None,
                personal_likeness=False,
                classification=request.classification,
            )
            if not self.state.finish(assignment, "completed", result_digest=output.digest()):
                raise ValueError("persona_generation_task_cancelled")
            self._record(assignment, "succeeded", output.digest())
            recorded = True
            self.policy.require(principal, project, request)
            pin = PersonaGenerationRunPin(
                run_id=run.run_id,
                task_id=run.task_id,
                assignment_id=run.assignment_id,
                dispatch_lease_id=run.dispatch_lease_id,
                input_digest=run.input_digest,
                expected_binding_digest=run.binding_digest,
            )
            generated_source = self.admission.admit(
                principal,
                project,
                output=output,
                run_pin=pin,
                content=content,
            )
            return GeneratedPersona(generated_source, output, pin, content)
        except Exception:
            try:
                self.state.finish(assignment, "failed")
            finally:
                if not recorded:
                    try:
                        self._record(assignment, "failed", hashlib.sha256(b"persona_generation_failed").hexdigest())
                    except Exception:
                        pass  # Unknown terminal state never authorizes a returned asset.
            raise

    def _record(self, assignment, state, digest):
        self.registry.record_result(
            tenant_id=assignment["tenant_id"],
            project_id=assignment["project_id"],
            run_id=assignment["run_id"],
            assignment_id=assignment["assignment_id"],
            dispatch_lease_id=assignment["lease_id"],
            terminal_state=state,
            result_digest=digest,
        )

"""Compose generation with existing explicit policy and delegated asset inspection."""

import time
import uuid

from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaVideoPolicy


class PersonaGeneratedAssets:
    def __init__(
        self, *, generator, image_assets=None, image_policy=None, video_assets=None, video_policy=None, clock=time.time
    ):
        self.generator, self.clock = generator, clock
        self.targets = {
            "image": (image_assets, image_policy, PersonaImagePolicy),
            "video": (video_assets, video_policy, PersonaVideoPolicy),
        }

    def create(self, principal, project, request):
        # Check enabled target before any generation or identity write.
        kind = request.recipe.media_kind
        assets, policy, policy_type = self.targets[kind]
        if assets is None or policy is None:
            raise ValueError("persona_generation_asset_kind_disabled")
        generated = self.generator.generate(principal, project, request)
        output = generated.output
        purposes = ("inspect", "store", "preview") + (("publish",) if request.publish else ())
        terms = dict(
            tenant_id=principal.tenant_id,
            project_id=project,
            policy_binding=f"generated-{uuid.uuid4()}",
            revision=1,
            source=generated.source,
            license=output.license,
            consent=None,
            origin_kind="generated",
            personal_likeness=False,
            classification=output.classification,
            subjects=(principal.subject_id,),
            purposes=purposes,
            expires_at_ms=int((self.clock() + request.valid_for_seconds) * 1000),
        )
        permission = policy_type(**terms, **({"media_kind": "video"} if kind == "video" else {}))
        policy.install(principal, permission, expected_revision=0)
        # Both source generation and inspection retain independent queue-owned
        # Tasks/runs. Generated content cannot bypass the normal media decoder.
        return getattr(assets, f"admit_{kind}")(
            principal,
            project,
            content=generated.content,
            media_type=output.media_type,
            origin_binding=generated.source.source_id,
            license_binding=output.license.source_id,
            consent_binding=None,
        )

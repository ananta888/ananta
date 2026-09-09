"""Explicit bounded procedural generation request; no arbitrary prompt/provider."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generated_source import receipt_digest
from agent.models.persona_media import ClosedModel


class PersonaGenerationRecipe(ClosedModel):
    profile: Literal["procedural-avatar-v1"]
    media_kind: Literal["image", "video"]
    palette: Literal["indigo", "teal", "amber"]


class PersonaGenerationRequest(ClosedModel):
    recipe: PersonaGenerationRecipe
    license: PersonaSourcePin
    classification: Literal["synthetic", "test_only"]
    publish: StrictBool = False
    valid_for_seconds: Annotated[StrictInt, Field(ge=60, le=3600)] = 900

    def digest(self):
        return receipt_digest(self.model_dump(mode="json"))


def admission_digest(request, source):
    return receipt_digest({"request": request.model_dump(mode="json"), "recipe_source": source.model_dump(mode="json")})

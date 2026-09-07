"""Compatible image-query facade over the bounded media-discovery engine."""

import time

from agent.services.persona_asset_query import PersonaAssetQuery


class PersonaImageQuery(PersonaAssetQuery):
    def __init__(self, *, policy, catalog, images, cursors, monotonic=time.monotonic):
        super().__init__(
            policy=policy, catalog=catalog, references=images, cursors=cursors, kind="image", monotonic=monotonic
        )

    @property
    def images(self):
        return self.references

    @images.setter
    def images(self, value):
        self.references = value

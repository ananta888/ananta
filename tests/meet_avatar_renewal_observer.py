"""Read-only evidence of actual control-plane renewal and new image hydration."""

import threading

from worker.meet_media.dialog_avatar_image_client import HubAvatarImageClient
from worker.meet_media.dialog_avatar_presentation import DialogAvatarPresentation


class AvatarRenewalObserver:
    def __init__(self, monkeypatch):
        self.condition = threading.Condition()
        self.generations = set()
        self.hydrations = []
        update = DialogAvatarPresentation.update
        fetch = HubAvatarImageClient.fetch

        def observe_update(presentation, receipt, controls, projection):
            result = update(presentation, receipt, controls, projection)
            with self.condition:
                self.generations.add(receipt["lease"]["generation"])
                self.condition.notify_all()
            return result

        def observe_fetch(client, binding, reference):
            result = fetch(client, binding, reference)
            with self.condition:
                self.hydrations.append({"generation": binding["generation"], "sha256": reference["sha256"]})
                self.condition.notify_all()
            return result

        monkeypatch.setattr(DialogAvatarPresentation, "update", observe_update)
        monkeypatch.setattr(HubAvatarImageClient, "fetch", observe_fetch)

    def require_renewed(self, image_hash):
        with self.condition:
            assert self.condition.wait_for(
                lambda: 2 in self.generations and {"generation": 2, "sha256": image_hash} in self.hydrations,
                timeout=75,
            ), {
                "lease_generations": sorted(self.generations),
                "hydrated_generations": [v["generation"] for v in self.hydrations],
            }

    def report(self):
        with self.condition:
            return {
                "lease_generations": sorted(self.generations),
                "hydrated_generations": [v["generation"] for v in self.hydrations],
            }

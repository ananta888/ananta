"""One assigned browser sequence; no Task admission, delegation or identity issuance."""

import time


class NativeCompanionSequence:
    def __init__(self, owner):
        self.owner = owner
        self.id = None

    def prepare(self, content, *, cooperate):
        probe = self.owner.page.evaluate("() => window.anantaMachine.avatar.sequenceProbe()")
        if probe != {"schema": "ananta.meet-avatar-sequence-probe.v1", "profile": "persona-sequence-v1",
                     "supported": True}:
            raise ValueError("meet_companion_native_sequence_unsupported")
        if self.owner.renew() is False:
            raise ValueError("meet_companion_native_sequence_renewal_busy")
        self.owner.pump.last_renew = time.monotonic()
        cooperate()
        receipt = self.owner.page.evaluate("(a) => window.anantaMachine.avatar.prepareSequence(a[0], a[1])",
            ["avatar:" + self.owner.client.assignment["session_id"], content])
        if (not isinstance(receipt, dict) or set(receipt) != {"schema", "id"}
                or receipt["schema"] != "ananta.meet-avatar-sequence-preparation.v1"
                or type(receipt["id"]) is not int or not 1 <= receipt["id"] <= 2048):
            raise ValueError("meet_companion_native_sequence_receipt_invalid")
        self.id = receipt["id"]
        deadline = time.monotonic() + 20
        while True:
            cooperate()
            status = self.owner.page.evaluate("(id) => window.anantaMachine.avatar.sequenceStatus(id)", self.id)
            if (not isinstance(status, dict) or set(status) != {"schema", "id", "state"}
                    or status["schema"] != "ananta.meet-avatar-sequence-status.v1" or status["id"] != self.id):
                raise ValueError("meet_companion_native_sequence_status_invalid")
            if status["state"] == "ready":
                return self.id
            if status["state"] != "preparing" or time.monotonic() >= deadline:
                raise ValueError("meet_companion_native_sequence_preparation_failed")
            time.sleep(.02)

    def activate(self, identity, speech_generation):
        if identity != self.id:
            raise ValueError("meet_companion_native_sequence_identity_invalid")
        if self.owner.avatar_generation is not None:
            self.owner.page.evaluate("(g) => window.anantaMachine.avatar.close(g)", self.owner.avatar_generation)
            self.owner.avatar_generation = None
        self.owner.sequence_active = True
        receipt = self.owner.page.evaluate(
            "(a) => window.anantaMachine.avatar.open(a[0], 'persona-sequence-v1', a[1])",
            ["avatar:" + self.owner.client.assignment["session_id"],
             {"id": identity, "speechGeneration": speech_generation}])
        if (not isinstance(receipt, dict) or set(receipt) != {
            "schema", "profile", "generation", "width", "height", "fps", "heartbeatMs", "expiresAt"
        } or receipt["schema"] != "ananta.meet-avatar-source.v1" or receipt["profile"] != "persona-sequence-v1"
                or type(receipt["generation"]) is not int or not 1 <= receipt["generation"] <= 1024
                or receipt["width"] != 256 or receipt["height"] != 256 or receipt["fps"] != 40
                or receipt["heartbeatMs"] != 2500 or type(receipt["expiresAt"]) is not int):
            raise ValueError("meet_companion_native_sequence_activation_invalid")
        self.owner.avatar_generation = receipt["generation"]
        self.owner.avatar_revision = self.owner.controls.state["controls"]["avatar"]
        self.owner.avatar_opened = time.monotonic()
        self.owner.sequence_active = True
        return self.owner.avatar_generation

    def quality(self, identity):
        return self.owner.page.evaluate("(id) => window.anantaMachine.avatar.sequenceQuality(id)", identity)

    def cancel(self):
        try:
            try:
                if self.owner.sequence_active:
                    if self.owner.avatar_generation is None:
                        self.owner.page.evaluate("() => window.anantaMachine.avatar.close()")
                    else:
                        self.owner.page.evaluate("(g) => window.anantaMachine.avatar.close(g)",
                                                 self.owner.avatar_generation)
                    self.owner.avatar_generation = None
            finally:
                if self.id is not None:
                    self.owner.page.evaluate("(id) => window.anantaMachine.avatar.cancelSequence(id)", self.id)
        finally:
            self.id = None
            self.owner.sequence_active = False

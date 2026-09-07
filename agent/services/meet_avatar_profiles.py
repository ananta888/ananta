"""Image-only avatar preparation never authorizes or requires voice output."""

from agent.services.meet_image_profile_binding import MeetImageProfileBinding


class MeetAvatarProfiles:
    def __init__(self, profiles, images):
        self._binding = MeetImageProfileBinding(profiles, images, ("image", "video"))
        self._images = images

    def prepare(self, principal, project, selection, purpose):
        assignment, binding = self._binding.prepare(principal, project, selection, purpose)
        self.require_current(principal, project, binding, assignment["reference"], purpose)
        return assignment, binding

    def select(self, principal, project, selection, purpose):
        reference, binding = self._binding.select(principal, project, selection)
        self.require_current(principal, project, binding, reference, purpose)
        return reference, binding

    def require_current(self, principal, project, binding, reference, purpose):
        self._binding.require_current(principal, project, binding, reference)
        self._images.require_current(principal, project, reference, purpose)
        # A profile mutation during asset authorization must not release a pin
        # checked only before the potentially stateful policy/storage boundary.
        self._binding.require_current(principal, project, binding, reference)

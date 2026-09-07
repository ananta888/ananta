"""Bind a bounded Meet turn to a current, explicitly selected Hub profile."""

from agent.services.meet_image_profile_binding import MeetImageProfileBinding


class MeetPersonaProfiles:
    # The legacy MP4 turn cannot independently omit speech or video. Declaring
    # the complete output set prevents it ignoring a resolved disabled state.
    _OUTPUTS = ("image", "voice", "video")

    def __init__(self, profiles, images):
        self.profiles, self.images = profiles, images
        self._binding = MeetImageProfileBinding(profiles, images, self._OUTPUTS)

    def prepare(self, principal, project, selection, purpose):
        return self._binding.prepare(principal, project, selection, purpose)

    def require_current(self, principal, project, binding, reference):
        return self._binding.require_current(principal, project, binding, reference)

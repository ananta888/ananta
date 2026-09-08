"""Format-specific clip start; reuse the independently fenced avatar lifecycle."""


class VideoAvatarBrowser:
    def __init__(self, browser, assignment, video):
        self.browser, self.assignment, self.video = browser, assignment, video

    def start(self, source_id):
        self.browser.start_video(
            source_id, self.video, tenant_id=self.assignment["tenant_id"], project_id=self.assignment["project_id"]
        )

    def status(self):
        return self.browser.status()

    def pulse(self):
        return self.browser.pulse()

    def close(self):
        return self.browser.close()

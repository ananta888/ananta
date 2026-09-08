"""One presentation activation; workspace lifetime remains owned by its controller."""


class BrowserWorkspaceFrameSource:
    def __init__(self, workspace, generation, session_id):
        self.workspace, self.generation, self.closed = workspace, generation, False
        # MDS-05 owns one session-scoped screen port, not a workspace-scoped
        # grant. The internal source retains its exact workspace/page epoch.
        self.source_id = "screen:" + session_id

    def take(self):
        if self.closed:
            raise ValueError("meet_browser_presentation_closed")
        return self.workspace.take(self.generation)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.workspace.discard_pending()

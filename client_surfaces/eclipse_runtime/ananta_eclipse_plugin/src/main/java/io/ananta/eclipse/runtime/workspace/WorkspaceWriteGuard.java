package io.ananta.eclipse.runtime.workspace;

import java.nio.file.Path;
import java.util.Objects;

public final class WorkspaceWriteGuard {
    public WriteDecision canWrite(String workspacePath, String filePath, boolean editorDirty) {
        if (editorDirty) {
            return WriteDecision.denied("dirty_editor_state");
        }
        Path workspace = Path.of(Objects.toString(workspacePath, "")).normalize();
        Path file = Path.of(Objects.toString(filePath, "")).normalize();
        if (workspace.toString().isBlank() || file.toString().isBlank()) {
            return WriteDecision.denied("invalid_workspace_or_file");
        }
        if (!file.startsWith(workspace)) {
            return WriteDecision.denied("outside_workspace");
        }
        return WriteDecision.allowed("workspace_write_allowed");
    }

    public record WriteDecision(boolean allowed, String reason) {
        public static WriteDecision allowed(String reason) {
            return new WriteDecision(true, reason);
        }

        public static WriteDecision denied(String reason) {
            return new WriteDecision(false, reason);
        }
    }
}

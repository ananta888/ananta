package io.ananta.eclipse.runtime.patch;

import io.ananta.eclipse.runtime.workspace.WorkspaceWriteGuard;

import java.util.Objects;

public final class ApprovalGatedPatchApplier {
    private final WorkspaceWriteGuard writeGuard;

    public ApprovalGatedPatchApplier(WorkspaceWriteGuard writeGuard) {
        this.writeGuard = Objects.requireNonNull(writeGuard, "writeGuard");
    }

    public PatchApplyDecision canApply(boolean approvalGranted, boolean userConfirmed, String workspacePath, String filePath) {
        if (!approvalGranted) {
            return PatchApplyDecision.denied("approval_required");
        }
        if (!userConfirmed) {
            return PatchApplyDecision.denied("explicit_confirmation_required");
        }
        WorkspaceWriteGuard.WriteDecision decision = writeGuard.canWrite(workspacePath, filePath, false);
        if (!decision.allowed()) {
            return PatchApplyDecision.denied(decision.reason());
        }
        return PatchApplyDecision.allowed("ready_to_apply");
    }

    public record PatchApplyDecision(boolean allowed, String reason) {
        public static PatchApplyDecision allowed(String reason) {
            return new PatchApplyDecision(true, reason);
        }

        public static PatchApplyDecision denied(String reason) {
            return new PatchApplyDecision(false, reason);
        }
    }
}

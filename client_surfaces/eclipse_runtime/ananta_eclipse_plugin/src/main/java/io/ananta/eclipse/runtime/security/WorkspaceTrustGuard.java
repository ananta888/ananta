package io.ananta.eclipse.runtime.security;

import java.util.Set;

public final class WorkspaceTrustGuard {
    private final Set<String> trustedWorkspacePaths;

    public WorkspaceTrustGuard(Set<String> trustedWorkspacePaths) {
        this.trustedWorkspacePaths = trustedWorkspacePaths == null ? Set.of() : Set.copyOf(trustedWorkspacePaths);
    }

    public TrustDecision evaluate(String workspacePath, boolean writeAction) {
        boolean trusted = trustedWorkspacePaths.contains(workspacePath);
        if (writeAction && !trusted) {
            return new TrustDecision(false, "workspace_not_trusted");
        }
        return new TrustDecision(true, "trusted");
    }

    public record TrustDecision(boolean allowed, String reason) {
    }
}

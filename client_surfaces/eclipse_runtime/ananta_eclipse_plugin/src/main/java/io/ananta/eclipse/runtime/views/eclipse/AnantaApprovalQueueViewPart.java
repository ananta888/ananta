package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseApprovalRuntimeView;

public final class AnantaApprovalQueueViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaApprovalQueueViewPart() {
        super("Ananta Approval Queue");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseApprovalRuntimeView runtimeView = new EclipseApprovalRuntimeView(
                session.services().apiClient(),
                session.services().capabilityGate()
        );
        return RuntimeViewResponseFormatter.block("approvals", runtimeView.loadPendingApprovals())
                + "\n\napproval_actions=approve,reject (capability-gated)";
    }
}

package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseRepairRuntimeView;

public final class AnantaRepairViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaRepairViewPart() {
        super("Ananta Repair Explorer");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseRepairRuntimeView.RepairExplorerModel model = new EclipseRepairRuntimeView(
                session.services().apiClient(),
                session.services().capabilityGate()
        ).loadRepairExplorer(null);
        return RuntimeViewResponseFormatter.block("failed_tasks", model.sessionsResponse())
                + "\n\nread_only_by_default=" + model.readOnlyByDefault()
                + ", no_execution_on_open_or_refresh=" + model.noExecutionOnOpenOrRefresh();
    }
}

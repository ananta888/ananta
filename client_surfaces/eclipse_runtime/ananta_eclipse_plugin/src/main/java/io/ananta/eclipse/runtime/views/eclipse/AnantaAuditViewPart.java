package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseAuditRuntimeView;

public final class AnantaAuditViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaAuditViewPart() {
        super("Ananta Audit Explorer");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseAuditRuntimeView.AuditExplorerModel model = new EclipseAuditRuntimeView(session.services().apiClient())
                .loadAuditExplorer(new EclipseAuditRuntimeView.AuditFilters(null, null, null));
        return RuntimeViewResponseFormatter.block("audit", model.response())
                + "\n\nredacted_preview:\n"
                + model.redactedPreview();
    }
}

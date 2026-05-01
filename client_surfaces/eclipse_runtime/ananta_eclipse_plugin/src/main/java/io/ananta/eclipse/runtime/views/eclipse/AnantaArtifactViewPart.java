package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseArtifactRuntimeView;

public final class AnantaArtifactViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaArtifactViewPart() {
        super("Ananta Artifact View");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseArtifactRuntimeView.ArtifactViewModel model = new EclipseArtifactRuntimeView(session.services().apiClient())
                .loadArtifactViews(null);
        return RuntimeViewResponseFormatter.block("artifacts", model.artifactListResponse())
                + "\n\nbounded_rendering=" + model.boundedRendering()
                + ", links_to_tasks_visible=" + model.linksToTasksVisible();
    }
}

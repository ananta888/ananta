package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseTaskRuntimeView;

public final class AnantaTaskListViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaTaskListViewPart() {
        super("Ananta Task List");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseTaskRuntimeView.TaskViewModel model = new EclipseTaskRuntimeView(session.services().apiClient())
                .loadTaskViews(null);
        return RuntimeViewResponseFormatter.block("tasks", model.taskListResponse())
                + "\n\nstale_or_missing_state=" + model.staleOrMissingState()
                + ", review_required_marker=" + model.reviewRequiredMarkerVisible()
                + ", next_step_marker=" + model.nextStepMarkerVisible();
    }
}

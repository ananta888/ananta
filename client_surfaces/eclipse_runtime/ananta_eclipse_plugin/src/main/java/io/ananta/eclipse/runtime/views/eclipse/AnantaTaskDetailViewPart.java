package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;
import io.ananta.eclipse.runtime.views.EclipseTaskRuntimeView;

public final class AnantaTaskDetailViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaTaskDetailViewPart() {
        super("Ananta Task Detail");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        EclipseTaskRuntimeView.TaskViewModel model = new EclipseTaskRuntimeView(session.services().apiClient())
                .loadTaskViews(null);
        String detail = model.taskDetailResponse() == null
                ? "No task selected yet. Open a task id from Task List and retry."
                : RuntimeViewResponseFormatter.block("task_detail", model.taskDetailResponse());
        return RuntimeViewResponseFormatter.block("tasks", model.taskListResponse())
                + "\n\n"
                + detail;
    }
}

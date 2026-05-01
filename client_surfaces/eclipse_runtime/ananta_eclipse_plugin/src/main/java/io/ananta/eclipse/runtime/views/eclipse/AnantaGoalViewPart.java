package io.ananta.eclipse.runtime.views.eclipse;

import io.ananta.eclipse.runtime.commands.GoalSubmissionRuntimePanel;
import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeBootstrap;
import io.ananta.eclipse.runtime.platform.AnantaRuntimeSession;

import java.util.List;

public final class AnantaGoalViewPart extends AbstractAnantaRuntimeViewPart {
    public AnantaGoalViewPart() {
        super("Ananta Goal Panel");
    }

    @Override
    protected String renderContent(AnantaRuntimeSession session) {
        GoalSubmissionRuntimePanel.GoalSubmissionPreview preview = new GoalSubmissionRuntimePanel(session.services().apiClient())
                .buildPreview(
                        "Analyze current workspace context",
                        "repository_understanding",
                        AnantaRuntimeBootstrap.profile().getProfileId(),
                        new EclipseContextCaptureRuntime().capture(
                                new EclipseContextCaptureRuntime.WorkspaceState(null, null, null, List.of()),
                                new EclipseContextCaptureRuntime.EditorState(null, null, null)
                        ).toPreviewMap()
                );
        return "goal_preview_only=true"
                + "\noperation_preset=" + preview.operationPreset()
                + "\nprofile_id=" + preview.profileId()
                + "\nuser_review_required_before_send=" + preview.userReviewRequiredBeforeSend()
                + "\ncontext_preview=" + preview.contextPreview()
                + "\n\nuse command handlers to submit goals.";
    }
}

package io.ananta.eclipse.runtime.platform;

import org.eclipse.ui.IPageLayout;
import org.eclipse.ui.IPerspectiveFactory;

public final class AnantaPerspectiveFactory implements IPerspectiveFactory {
    public static final String PERSPECTIVE_ID = "io.ananta.eclipse.perspective";

    @Override
    public void createInitialLayout(IPageLayout layout) {
        String editorArea = layout.getEditorArea();
        layout.addView("io.ananta.eclipse.view.chat", IPageLayout.RIGHT, 0.62f, editorArea);
        layout.addView("io.ananta.eclipse.view.task_list", IPageLayout.BOTTOM, 0.65f, editorArea);
        layout.addView("io.ananta.eclipse.view.status", IPageLayout.BOTTOM, 0.75f, "io.ananta.eclipse.view.chat");
    }
}

package io.ananta.eclipse.runtime.chat;

import io.ananta.eclipse.runtime.context.EclipseContextCaptureRuntime;

import java.util.List;

public final class ChatContextSelector {
    public SelectedChatContext select(
            boolean includeSelection,
            boolean includeFileExcerpt,
            EclipseContextCaptureRuntime.BoundedContextPayload payload
    ) {
        EclipseContextCaptureRuntime.BoundedContextPayload safe = payload == null
                ? new EclipseContextCaptureRuntime().capture(null, null)
                : payload;
        return new SelectedChatContext(
                includeSelection ? safe.selectionText() : null,
                includeFileExcerpt ? safe.fileContentExcerpt() : null,
                safe.selectedPaths(),
                safe.rejectedPaths(),
                safe.bounded(),
                true
        );
    }

    public record SelectedChatContext(
            String selectionText,
            String fileContentExcerpt,
            List<String> selectedPaths,
            List<String> rejectedPaths,
            boolean bounded,
            boolean userReviewRequired
    ) {
        public SelectedChatContext {
            selectedPaths = selectedPaths == null ? List.of() : List.copyOf(selectedPaths);
            rejectedPaths = rejectedPaths == null ? List.of() : List.copyOf(rejectedPaths);
        }
    }
}

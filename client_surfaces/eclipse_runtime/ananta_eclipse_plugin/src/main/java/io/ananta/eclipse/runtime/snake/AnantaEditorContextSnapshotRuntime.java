package io.ananta.eclipse.runtime.snake;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Objects;

public final class AnantaEditorContextSnapshotRuntime {
    public record SelectionRange(int startOffset, int endOffset) {
        public SelectionRange {
            if (startOffset < 0 || endOffset < startOffset) {
                throw new IllegalArgumentException("selection_range_invalid");
            }
        }
    }

    public record EclipseContextSnapshot(
            String projectName,
            String filePathRef,
            String editorType,
            SelectionRange selectionRange,
            String sourceKind,
            boolean includesFileContent
    ) {
    }

    public EclipseContextSnapshot captureSnapshot(
            String projectName,
            String filePath,
            String editorType,
            SelectionRange selectionRange
    ) {
        String normalizedProject = Objects.toString(projectName, "").trim();
        String normalizedFilePath = Objects.toString(filePath, "").trim();
        String normalizedEditorType = Objects.toString(editorType, "").trim();
        SelectionRange normalizedRange = selectionRange == null ? new SelectionRange(0, 0) : selectionRange;
        return new EclipseContextSnapshot(
                normalizedProject,
                hashPathRef(normalizedFilePath),
                normalizedEditorType.isBlank() ? "unknown" : normalizedEditorType,
                normalizedRange,
                "metadata_only",
                false
        );
    }

    private static String hashPathRef(String filePath) {
        if (filePath.isBlank()) {
            return "path_ref:unknown";
        }
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(filePath.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (byte value : hash) {
                hex.append(String.format("%02x", value));
            }
            return "path_ref:" + hex.substring(0, 24);
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException("sha256_not_available", exc);
        }
    }
}

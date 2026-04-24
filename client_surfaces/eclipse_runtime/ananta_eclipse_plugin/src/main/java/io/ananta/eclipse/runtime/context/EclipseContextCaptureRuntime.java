package io.ananta.eclipse.runtime.context;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class EclipseContextCaptureRuntime {
    public static final int DEFAULT_MAX_SELECTION_CHARS = 5000;
    public static final int DEFAULT_MAX_FILE_EXCERPT_CHARS = 5000;
    public static final int DEFAULT_MAX_PATHS = 20;

    private final int maxSelectionChars;
    private final int maxFileExcerptChars;
    private final int maxPaths;

    public EclipseContextCaptureRuntime() {
        this(DEFAULT_MAX_SELECTION_CHARS, DEFAULT_MAX_FILE_EXCERPT_CHARS, DEFAULT_MAX_PATHS);
    }

    public EclipseContextCaptureRuntime(int maxSelectionChars, int maxFileExcerptChars, int maxPaths) {
        if (maxSelectionChars <= 0 || maxFileExcerptChars <= 0 || maxPaths <= 0) {
            throw new IllegalArgumentException("context bounds must be > 0");
        }
        this.maxSelectionChars = maxSelectionChars;
        this.maxFileExcerptChars = maxFileExcerptChars;
        this.maxPaths = maxPaths;
    }

    public BoundedContextPayload capture(WorkspaceState workspaceState, EditorState editorState) {
        WorkspaceState workspace = workspaceState == null ? new WorkspaceState(null, null, null, List.of()) : workspaceState;
        EditorState editor = editorState == null ? new EditorState(null, null, null) : editorState;
        String workspacePath = clean(workspace.workspacePath(), 400);
        String projectName = clean(workspace.projectName(), 200);
        String activeFilePath = clean(workspace.activeFilePath(), 400);
        List<String> selectedPaths = new ArrayList<>();
        List<String> rejectedPaths = new ArrayList<>();
        for (String candidate : workspace.selectedPaths()) {
            if (selectedPaths.size() >= maxPaths) {
                break;
            }
            String normalized = clean(candidate, 400);
            if (normalized.isBlank()) {
                continue;
            }
            if (!workspacePath.isBlank() && !normalized.startsWith(workspacePath)) {
                rejectedPaths.add(normalized);
                continue;
            }
            selectedPaths.add(normalized);
        }
        String selectionTextRaw = clean(editor.selectionText(), maxSelectionChars * 2);
        String selectionText = clip(selectionTextRaw, maxSelectionChars);
        String fileExcerptRaw = clean(editor.fileContentExcerpt(), maxFileExcerptChars * 2);
        String fileExcerpt = clip(fileExcerptRaw, maxFileExcerptChars);
        return new BoundedContextPayload(
                workspacePath.isBlank() ? null : workspacePath,
                projectName.isBlank() ? null : projectName,
                activeFilePath.isBlank() ? null : activeFilePath,
                clean(editor.filePath(), 400),
                selectedPaths,
                rejectedPaths,
                selectionText.isBlank() ? null : selectionText,
                fileExcerpt.isBlank() ? null : fileExcerpt,
                selectionTextRaw.length() > maxSelectionChars,
                fileExcerptRaw.length() > maxFileExcerptChars,
                true,
                true
        );
    }

    private static String clean(String value, int maxChars) {
        String normalized = Objects.toString(value, "").trim();
        if (normalized.isEmpty()) {
            return "";
        }
        return normalized.substring(0, Math.min(maxChars, normalized.length()));
    }

    private static String clip(String value, int maxChars) {
        if (value == null || value.length() <= maxChars) {
            return Objects.toString(value, "");
        }
        return value.substring(0, maxChars);
    }

    private static String toJson(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String text) {
            return "\"" + escapeJson(text) + "\"";
        }
        if (value instanceof Number || value instanceof Boolean) {
            return String.valueOf(value);
        }
        if (value instanceof Map<?, ?> map) {
            List<String> entries = new ArrayList<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                String key = Objects.toString(entry.getKey(), "");
                entries.add("\"" + escapeJson(key) + "\":" + toJson(entry.getValue()));
            }
            return "{" + String.join(",", entries) + "}";
        }
        if (value instanceof List<?> list) {
            List<String> entries = new ArrayList<>();
            for (Object item : list) {
                entries.add(toJson(item));
            }
            return "[" + String.join(",", entries) + "]";
        }
        return "\"" + escapeJson(String.valueOf(value)) + "\"";
    }

    private static String escapeJson(String value) {
        return Objects.toString(value, "")
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }

    public record WorkspaceState(
            String workspacePath,
            String projectName,
            String activeFilePath,
            List<String> selectedPaths
    ) {
        public WorkspaceState {
            selectedPaths = selectedPaths == null ? List.of() : List.copyOf(selectedPaths);
        }
    }

    public record EditorState(
            String filePath,
            String selectionText,
            String fileContentExcerpt
    ) {
    }

    public record BoundedContextPayload(
            String workspacePath,
            String projectName,
            String activeFilePath,
            String editorFilePath,
            List<String> selectedPaths,
            List<String> rejectedPaths,
            String selectionText,
            String fileContentExcerpt,
            boolean selectionClipped,
            boolean fileContentClipped,
            boolean bounded,
            boolean userReviewRequiredBeforeSend
    ) {
        public BoundedContextPayload {
            selectedPaths = selectedPaths == null ? List.of() : List.copyOf(selectedPaths);
            rejectedPaths = rejectedPaths == null ? List.of() : List.copyOf(rejectedPaths);
        }

        public Map<String, Object> toPreviewMap() {
            Map<String, Object> preview = new LinkedHashMap<>();
            preview.put("schema", "eclipse_runtime_context_preview_v1");
            preview.put("workspace_path", workspacePath);
            preview.put("project_name", projectName);
            preview.put("active_file_path", activeFilePath);
            preview.put("editor_file_path", editorFilePath);
            preview.put("selected_paths", selectedPaths);
            preview.put("selected_paths_count", selectedPaths.size());
            preview.put("rejected_paths", rejectedPaths);
            preview.put("selection_clipped", selectionClipped);
            preview.put("file_content_clipped", fileContentClipped);
            preview.put("bounded", bounded);
            preview.put("user_review_required_before_send", userReviewRequiredBeforeSend);
            return preview;
        }

        public String toContextJson() {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("schema", "client_bounded_context_payload_v1");
            payload.put("workspace_path", workspacePath);
            payload.put("project_name", projectName);
            payload.put("active_file_path", activeFilePath);
            payload.put("editor_file_path", editorFilePath);
            payload.put("selected_paths", selectedPaths);
            payload.put("rejected_paths", rejectedPaths);
            payload.put("selection_text", selectionText);
            payload.put("file_content_excerpt", fileContentExcerpt);
            payload.put("selection_clipped", selectionClipped);
            payload.put("file_content_clipped", fileContentClipped);
            payload.put("bounded", bounded);
            payload.put("implicit_unrelated_paths_included", false);
            return toJson(payload);
        }
    }
}

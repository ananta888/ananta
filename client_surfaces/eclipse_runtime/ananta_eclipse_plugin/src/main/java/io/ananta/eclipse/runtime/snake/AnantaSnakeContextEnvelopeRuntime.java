package io.ananta.eclipse.runtime.snake;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

public final class AnantaSnakeContextEnvelopeRuntime {
    public SnakeContextEnvelope build(
            String ideZone,
            AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot snapshot,
            AnantaSnakePredictionEvent predictionEvent,
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        return new SnakeContextEnvelope(
                Objects.toString(ideZone, "unknown").trim(),
                snapshot,
                predictionEvent,
                Objects.toString(policyDecisionRef, "policy_default_deny").trim(),
                deniedContextRefs == null ? List.of() : List.copyOf(deniedContextRefs),
                artifactRefs == null ? List.of() : List.copyOf(artifactRefs)
        );
    }

    public record SnakeContextEnvelope(
            String ideZone,
            AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot eclipseContextSnapshot,
            AnantaSnakePredictionEvent predictionEvent,
            String policyDecisionRef,
            List<String> deniedContextRefs,
            List<String> artifactRefs
    ) {
        public SnakeContextEnvelope {
            deniedContextRefs = deniedContextRefs == null ? List.of() : List.copyOf(deniedContextRefs);
            artifactRefs = artifactRefs == null ? List.of() : List.copyOf(artifactRefs);
        }

        public String toJson() {
            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("schema", "eclipse_snake_context_envelope_v1");
            payload.put("ide_zone", ideZone);
            payload.put("eclipse_context_snapshot", snapshotMap());
            payload.put("prediction_event", predictionMap());
            payload.put("policy_decision_ref", policyDecisionRef);
            payload.put("denied_context_refs", deniedContextRefs);
            payload.put("artifact_refs", artifactRefs);
            return toJsonValue(payload);
        }

        private Map<String, Object> snapshotMap() {
            AnantaEditorContextSnapshotRuntime.EclipseContextSnapshot snapshot = eclipseContextSnapshot;
            Map<String, Object> mapped = new LinkedHashMap<>();
            mapped.put("project_name", snapshot.projectName());
            mapped.put("file_path_ref", snapshot.filePathRef());
            mapped.put("editor_type", snapshot.editorType());
            mapped.put("selection_start", snapshot.selectionRange().startOffset());
            mapped.put("selection_end", snapshot.selectionRange().endOffset());
            mapped.put("source_kind", snapshot.sourceKind());
            mapped.put("includes_file_content", snapshot.includesFileContent());
            return mapped;
        }

        private Map<String, Object> predictionMap() {
            AnantaSnakePredictionEvent prediction = predictionEvent;
            Map<String, Object> mapped = new LinkedHashMap<>();
            mapped.put("intent_kind", prediction.intentKind());
            mapped.put("confidence", prediction.confidence());
            mapped.put("evidence", prediction.evidence());
            mapped.put("expires_at", prediction.expiresAtEpochMillis());
            return mapped;
        }
    }

    private static String toJsonValue(Object value) {
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
                entries.add("\"" + escapeJson(Objects.toString(entry.getKey(), "")) + "\":" + toJsonValue(entry.getValue()));
            }
            return "{" + String.join(",", entries) + "}";
        }
        if (value instanceof List<?> list) {
            List<String> entries = new ArrayList<>();
            for (Object item : list) {
                entries.add(toJsonValue(item));
            }
            return "[" + String.join(",", entries) + "]";
        }
        return "\"" + escapeJson(Objects.toString(value, "")) + "\"";
    }

    private static String escapeJson(String value) {
        return Objects.toString(value, "")
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }
}

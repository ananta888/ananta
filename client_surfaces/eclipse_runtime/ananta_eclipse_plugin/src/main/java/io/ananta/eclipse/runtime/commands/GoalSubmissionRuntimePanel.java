package io.ananta.eclipse.runtime.commands;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class GoalSubmissionRuntimePanel {
    private static final Pattern TASK_ID_PATTERN = Pattern.compile("\"task_id\"\\s*:\\s*\"([^\"]+)\"");
    private static final Pattern GOAL_ID_PATTERN = Pattern.compile("\"goal_id\"\\s*:\\s*\"([^\"]+)\"");
    private static final Pattern ARTIFACT_ID_PATTERN = Pattern.compile("\"artifact_id\"\\s*:\\s*\"([^\"]+)\"");

    private final AnantaApiClient apiClient;

    public GoalSubmissionRuntimePanel(AnantaApiClient apiClient) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
    }

    public GoalSubmissionPreview buildPreview(
            String goalText,
            String operationPreset,
            String profileId,
            Map<String, Object> contextPreview
    ) {
        String normalizedGoal = Objects.toString(goalText, "").trim();
        if (normalizedGoal.isBlank()) {
            throw new IllegalArgumentException("goalText must not be blank");
        }
        return new GoalSubmissionPreview(
                normalizedGoal,
                Objects.toString(operationPreset, "").trim(),
                Objects.toString(profileId, "").trim(),
                contextPreview == null ? Map.of() : new LinkedHashMap<>(contextPreview),
                true
        );
    }

    public GoalSubmissionResult submit(GoalSubmissionPreview preview, String contextJson, String commandId) {
        GoalSubmissionPreview normalizedPreview = Objects.requireNonNull(preview, "preview");
        ClientResponse response = apiClient.submitGoal(
                normalizedPreview.goalText(),
                contextJson,
                normalizedPreview.operationPreset(),
                commandId,
                normalizedPreview.profileId()
        );
        return new GoalSubmissionResult(response, buildResultLinks(response), normalizedPreview.contextPreview());
    }

    private List<Map<String, String>> buildResultLinks(ClientResponse response) {
        if (response == null || response.getResponseBody() == null) {
            return List.of();
        }
        String body = response.getResponseBody();
        List<Map<String, String>> links = new ArrayList<>();
        String taskId = extract(body, TASK_ID_PATTERN);
        String goalId = extract(body, GOAL_ID_PATTERN);
        String artifactId = extract(body, ARTIFACT_ID_PATTERN);
        if (!taskId.isBlank()) {
            links.add(link("task", "/tasks/" + taskId));
            links.add(link("task_artifacts", "/artifacts?task_id=" + taskId));
        }
        if (!goalId.isBlank()) {
            links.add(link("goal", "/goals/" + goalId));
        }
        if (!artifactId.isBlank()) {
            links.add(link("artifact", "/artifacts/" + artifactId));
        }
        if (links.isEmpty()) {
            links.add(link("tasks", "/tasks"));
            links.add(link("artifacts", "/artifacts"));
        }
        return links;
    }

    private static String extract(String source, Pattern pattern) {
        Matcher matcher = pattern.matcher(source);
        if (!matcher.find()) {
            return "";
        }
        return Objects.toString(matcher.group(1), "").trim();
    }

    private static Map<String, String> link(String name, String relativePath) {
        Map<String, String> link = new LinkedHashMap<>();
        link.put("name", name);
        link.put("path", relativePath);
        return link;
    }

    public record GoalSubmissionPreview(
            String goalText,
            String operationPreset,
            String profileId,
            Map<String, Object> contextPreview,
            boolean userReviewRequiredBeforeSend
    ) {
        public GoalSubmissionPreview {
            contextPreview = contextPreview == null ? Map.of() : new LinkedHashMap<>(contextPreview);
        }
    }

    public record GoalSubmissionResult(
            ClientResponse response,
            List<Map<String, String>> resultLinks,
            Map<String, Object> contextPreview
    ) {
        public GoalSubmissionResult {
            resultLinks = resultLinks == null ? List.of() : List.copyOf(resultLinks);
            contextPreview = contextPreview == null ? Map.of() : new LinkedHashMap<>(contextPreview);
        }
    }
}

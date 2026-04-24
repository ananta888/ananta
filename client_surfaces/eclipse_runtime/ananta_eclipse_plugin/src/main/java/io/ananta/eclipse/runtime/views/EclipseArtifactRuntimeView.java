package io.ananta.eclipse.runtime.views;

import io.ananta.eclipse.runtime.core.AnantaApiClient;
import io.ananta.eclipse.runtime.core.ClientResponse;

import java.util.List;
import java.util.Locale;
import java.util.Objects;

public final class EclipseArtifactRuntimeView {
    private final AnantaApiClient apiClient;

    public EclipseArtifactRuntimeView(AnantaApiClient apiClient) {
        this.apiClient = Objects.requireNonNull(apiClient, "apiClient");
    }

    public ArtifactViewModel loadArtifactViews(String selectedArtifactId) {
        ClientResponse artifactListResponse = apiClient.listArtifacts();
        ClientResponse artifactDetailResponse = null;
        String normalizedArtifactId = Objects.toString(selectedArtifactId, "").trim();
        if (!normalizedArtifactId.isBlank()) {
            artifactDetailResponse = apiClient.getArtifact(normalizedArtifactId);
        }
        return new ArtifactViewModel(
                artifactListResponse,
                artifactDetailResponse,
                true,
                true
        );
    }

    public DiffRenderModel renderDiffReferences(String artifactType, List<DiffHunkReference> hunks) {
        String renderMode = renderModeForType(artifactType);
        List<DiffHunkReference> references = hunks == null ? List.of() : List.copyOf(hunks);
        return new DiffRenderModel(
                renderMode,
                references,
                renderMode.equals("raw_text"),
                true
        );
    }

    private static String renderModeForType(String artifactType) {
        String normalized = Objects.toString(artifactType, "").trim().toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "diff", "review", "patch", "proposal" -> "proposal_review";
            case "markdown", "md", "text/markdown" -> "markdown_text";
            case "summary", "text" -> "summary";
            default -> "raw_text";
        };
    }

    public record ArtifactViewModel(
            ClientResponse artifactListResponse,
            ClientResponse artifactDetailResponse,
            boolean linksToTasksVisible,
            boolean boundedRendering
    ) {
    }

    public record DiffRenderModel(
            String renderMode,
            List<DiffHunkReference> fileReferences,
            boolean fallbackToBrowser,
            boolean neverAutoApplyVisibleChanges
    ) {
        public DiffRenderModel {
            fileReferences = fileReferences == null ? List.of() : List.copyOf(fileReferences);
        }
    }

    public record DiffHunkReference(
            String path,
            Integer line
    ) {
    }
}

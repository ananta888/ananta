package io.ananta.eclipse.runtime.patch;

import java.util.List;
import java.util.Objects;

public final class PatchPreviewViewModel {
    private final List<PatchHunk> hunks;

    public PatchPreviewViewModel(List<PatchHunk> hunks) {
        this.hunks = hunks == null ? List.of() : List.copyOf(hunks);
    }

    public List<PatchHunk> hunks() {
        return hunks;
    }

    public boolean neverAutoApply() {
        return true;
    }

    public boolean hasSelectedHunks() {
        return hunks.stream().anyMatch(PatchHunk::selected);
    }

    public record PatchHunk(String path, int line, String preview, boolean selected) {
        public PatchHunk {
            path = Objects.toString(path, "").trim();
            preview = Objects.toString(preview, "");
        }
    }
}

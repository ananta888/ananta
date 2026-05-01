package io.ananta.eclipse.runtime.project;

import io.ananta.eclipse.runtime.workspace.WorkspaceWriteGuard;

import java.util.List;
import java.util.Objects;

public final class GeneratedProjectImporter {
    private final WorkspaceWriteGuard writeGuard;

    public GeneratedProjectImporter(WorkspaceWriteGuard writeGuard) {
        this.writeGuard = Objects.requireNonNull(writeGuard, "writeGuard");
    }

    public ImportPreview preview(String workspacePath, List<String> generatedFiles) {
        List<String> files = generatedFiles == null ? List.of() : List.copyOf(generatedFiles);
        boolean allInside = files.stream().allMatch(file -> writeGuard.canWrite(workspacePath, file, false).allowed());
        return new ImportPreview(files, allInside, true);
    }

    public record ImportPreview(List<String> files, boolean importAllowed, boolean reviewRequired) {
        public ImportPreview {
            files = files == null ? List.of() : List.copyOf(files);
        }
    }
}

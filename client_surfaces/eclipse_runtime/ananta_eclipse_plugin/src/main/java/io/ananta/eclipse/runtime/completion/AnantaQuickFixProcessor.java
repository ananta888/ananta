package io.ananta.eclipse.runtime.completion;

import java.util.List;
import java.util.Objects;

public final class AnantaQuickFixProcessor {
    public QuickFixProposal build(String markerId, String title, List<String> affectedFiles, boolean policyAllowed) {
        return new QuickFixProposal(
                Objects.toString(markerId, "").trim(),
                Objects.toString(title, "").trim(),
                affectedFiles == null ? List.of() : List.copyOf(affectedFiles),
                policyAllowed,
                true
        );
    }

    public record QuickFixProposal(
            String markerId,
            String title,
            List<String> affectedFiles,
            boolean policyAllowed,
            boolean previewRequired
    ) {
        public QuickFixProposal {
            affectedFiles = affectedFiles == null ? List.of() : List.copyOf(affectedFiles);
        }
    }
}

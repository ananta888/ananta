package io.ananta.eclipse.runtime.project;

import java.util.List;
import java.util.Objects;

public final class EvolutionProposalViewModel {
    public Proposal build(String proposalId, List<String> affectedFiles, List<String> risks, List<String> tests) {
        return new Proposal(
                Objects.toString(proposalId, "").trim(),
                affectedFiles == null ? List.of() : List.copyOf(affectedFiles),
                risks == null ? List.of() : List.copyOf(risks),
                tests == null ? List.of() : List.copyOf(tests),
                true
        );
    }

    public record Proposal(
            String proposalId,
            List<String> affectedFiles,
            List<String> risks,
            List<String> tests,
            boolean approvalGated
    ) {
        public Proposal {
            affectedFiles = affectedFiles == null ? List.of() : List.copyOf(affectedFiles);
            risks = risks == null ? List.of() : List.copyOf(risks);
            tests = tests == null ? List.of() : List.copyOf(tests);
        }
    }
}

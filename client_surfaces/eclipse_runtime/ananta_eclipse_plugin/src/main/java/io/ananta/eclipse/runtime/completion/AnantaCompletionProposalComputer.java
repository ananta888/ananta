package io.ananta.eclipse.runtime.completion;

import java.util.List;
import java.util.Objects;

public final class AnantaCompletionProposalComputer {
    public CompletionRequest buildRequest(String prefix, String contextJson, boolean policyAllowed) {
        return new CompletionRequest(
                clip(prefix, 400),
                clip(contextJson, 8000),
                policyAllowed,
                !policyAllowed
        );
    }

    public List<CompletionProposal> proposals(List<String> suggestions) {
        return (suggestions == null ? List.<String>of() : suggestions).stream()
                .map(value -> new CompletionProposal(clip(value, 1200), false))
                .toList();
    }

    private static String clip(String value, int maxChars) {
        String text = Objects.toString(value, "").trim();
        return text.substring(0, Math.min(maxChars, text.length()));
    }

    public record CompletionRequest(String prefix, String contextJson, boolean allowed, boolean policyLimited) {
    }

    public record CompletionProposal(String text, boolean autoApply) {
    }
}

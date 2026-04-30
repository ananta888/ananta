package com.ananta.mobile.runtime;

public final class ProviderCapability {
    public final boolean textGeneration;
    public final boolean speechToText;
    public final boolean embeddings;
    public final boolean streaming;
    public final boolean toolCalling;
    public final int maxContextTokens;

    public ProviderCapability(
            boolean textGeneration,
            boolean speechToText,
            boolean embeddings,
            boolean streaming,
            boolean toolCalling,
            int maxContextTokens
    ) {
        this.textGeneration = textGeneration;
        this.speechToText = speechToText;
        this.embeddings = embeddings;
        this.streaming = streaming;
        this.toolCalling = toolCalling;
        this.maxContextTokens = Math.max(0, maxContextTokens);
    }
}

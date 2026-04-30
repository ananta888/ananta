package com.ananta.mobile.runtime;

public interface EmbeddingProvider extends ModelProvider {
    float[] embed(String text);
}

package com.ananta.mobile.runtime;

public interface TextGenerationProvider extends ModelProvider {
    String generate(String prompt);

    void stopGeneration();
}

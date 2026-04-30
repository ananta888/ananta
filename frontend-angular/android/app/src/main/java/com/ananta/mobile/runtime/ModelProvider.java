package com.ananta.mobile.runtime;

public interface ModelProvider {
    String id();

    ProviderType type();

    ProviderCapability capability();

    void configure(ProviderConfig config);
}

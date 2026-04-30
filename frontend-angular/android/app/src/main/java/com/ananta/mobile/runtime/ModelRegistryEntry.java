package com.ananta.mobile.runtime;

public final class ModelRegistryEntry {
    public final String id;
    public final ProviderType type;
    public final String path;
    public final long bytes;
    public final String quantization;
    public final ProviderCapability capability;

    public ModelRegistryEntry(
            String id,
            ProviderType type,
            String path,
            long bytes,
            String quantization,
            ProviderCapability capability
    ) {
        this.id = id == null ? "" : id.trim();
        this.type = type == null ? ProviderType.TEXT_GENERATION : type;
        this.path = path == null ? "" : path.trim();
        this.bytes = Math.max(0L, bytes);
        this.quantization = quantization == null ? "unknown" : quantization.trim();
        this.capability = capability;
    }
}

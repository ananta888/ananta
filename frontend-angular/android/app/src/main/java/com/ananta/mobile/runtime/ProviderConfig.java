package com.ananta.mobile.runtime;

public final class ProviderConfig {
    public final String modelPath;
    public final int cpuThreads;
    public final int contextSize;
    public final float temperature;
    public final float topP;
    public final boolean preferGpu;

    public ProviderConfig(
            String modelPath,
            int cpuThreads,
            int contextSize,
            float temperature,
            float topP,
            boolean preferGpu
    ) {
        this.modelPath = modelPath == null ? "" : modelPath.trim();
        this.cpuThreads = Math.max(1, cpuThreads);
        this.contextSize = Math.max(256, contextSize);
        this.temperature = Math.max(0.0f, temperature);
        this.topP = Math.max(0.0f, Math.min(1.0f, topP));
        this.preferGpu = preferGpu;
    }
}

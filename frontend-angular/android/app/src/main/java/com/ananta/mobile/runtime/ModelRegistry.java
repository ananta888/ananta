package com.ananta.mobile.runtime;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * In-memory registry to keep a single source of truth for local model metadata.
 */
public final class ModelRegistry {
    private final Map<String, ModelRegistryEntry> entries = new LinkedHashMap<>();
    private String activeModelId;

    public synchronized void register(ModelRegistryEntry entry) {
        if (entry == null || entry.id.isBlank()) return;
        entries.put(entry.id, entry);
    }

    public synchronized List<ModelRegistryEntry> list() {
        return Collections.unmodifiableList(new ArrayList<>(entries.values()));
    }

    public synchronized ModelRegistryEntry get(String modelId) {
        return entries.get(modelId);
    }

    public synchronized boolean canActivateModel(String modelId) {
        if (modelId == null || modelId.isBlank()) return false;
        if (!entries.containsKey(modelId)) return false;
        return activeModelId == null || activeModelId.equals(modelId);
    }

    public synchronized boolean activateModel(String modelId) {
        if (!canActivateModel(modelId)) return false;
        activeModelId = modelId;
        return true;
    }

    public synchronized void clearActiveModel() {
        activeModelId = null;
    }

    public synchronized String activeModelId() {
        return activeModelId;
    }
}

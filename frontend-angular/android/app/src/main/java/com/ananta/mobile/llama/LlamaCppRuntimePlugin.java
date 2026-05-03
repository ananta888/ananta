package com.ananta.mobile.llama;

import com.ananta.mobile.llm.DownloadHelper;
import com.ananta.mobile.llm.LlmServerManager;
import com.ananta.mobile.llm.LlmServerManager.LlmSetupStatus;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Capacitor plugin for LLM runtime management.
 * Combines legacy JNI stub interface with new server-based llama.cpp management.
 */
@CapacitorPlugin(name = "LlamaCppRuntime")
public class LlamaCppRuntimePlugin extends Plugin {
    private static volatile boolean nativeAvailable;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private volatile LlmServerManager serverManager;

    static {
        boolean loaded = false;
        try {
            System.loadLibrary("ananta_llama_runtime");
            loaded = true;
        } catch (Throwable ignored) {
            loaded = false;
        }
        nativeAvailable = loaded;
    }

    @Override
    public void load() {
        super.load();
        serverManager = new LlmServerManager(getContext());
    }

    @Override
    protected void handleOnDestroy() {
        if (serverManager != null && serverManager.isServerRunning()) {
            serverManager.stopServer();
        }
        executor.shutdownNow();
        super.handleOnDestroy();
    }

    // ── Legacy JNI methods (kept for backward compatibility) ────────────

    @PluginMethod
    public void health(PluginCall call) {
        JSObject result = new JSObject();
        result.put("nativeAvailable", nativeAvailable);
        if (serverManager != null) {
            LlmSetupStatus status = serverManager.getFullStatus();
            result.put("serverInstalled", status.serverInstalled);
            result.put("modelInstalled", status.modelInstalled);
            result.put("serverRunning", status.serverRunning);
        }
        call.resolve(result);
    }

    @PluginMethod
    public void loadModel(PluginCall call) {
        if (!nativeAvailable) {
            call.reject("Native llama runtime unavailable.");
            return;
        }
        String modelPath = call.getString("modelPath");
        int threads = call.getInt("threads", 2);
        int contextSize = call.getInt("contextSize", 2048);
        if (modelPath == null || modelPath.isBlank()) {
            call.reject("modelPath is required.");
            return;
        }
        File file = new File(modelPath);
        if (!file.exists() || !file.isFile()) {
            call.reject("Model file not found: " + modelPath);
            return;
        }
        if (!modelPath.toLowerCase().endsWith(".gguf")) {
            call.reject("Only .gguf files are supported.");
            return;
        }

        boolean loaded = nativeLoadModel(modelPath, Math.max(1, threads), Math.max(256, contextSize));
        if (!loaded) {
            call.reject("nativeLoadModel failed.");
            return;
        }
        JSObject out = new JSObject();
        out.put("ok", true);
        out.put("modelPath", modelPath);
        out.put("threads", Math.max(1, threads));
        out.put("contextSize", Math.max(256, contextSize));
        call.resolve(out);
    }

    @PluginMethod
    public void generate(PluginCall call) {
        if (!nativeAvailable) {
            call.reject("Native llama runtime unavailable.");
            return;
        }
        String prompt = call.getString("prompt", "");
        int maxTokens = call.getInt("maxTokens", 128);
        String output = nativeGenerate(prompt == null ? "" : prompt, Math.max(1, maxTokens));
        JSObject out = new JSObject();
        out.put("text", output == null ? "" : output);
        call.resolve(out);
    }

    @PluginMethod
    public void stopGeneration(PluginCall call) {
        if (!nativeAvailable) {
            call.reject("Native llama runtime unavailable.");
            return;
        }
        nativeStopGeneration();
        JSObject out = new JSObject();
        out.put("stopped", true);
        call.resolve(out);
    }

    @PluginMethod
    public void unloadModel(PluginCall call) {
        if (!nativeAvailable) {
            call.reject("Native llama runtime unavailable.");
            return;
        }
        nativeUnloadModel();
        JSObject out = new JSObject();
        out.put("unloaded", true);
        call.resolve(out);
    }

    // ── Server-based LLM management (new) ───────────────────────────────

    @PluginMethod
    public void getLlmSetupStatus(PluginCall call) {
        LlmSetupStatus status = serverManager.getFullStatus();
        JSObject result = new JSObject();
        result.put("prootReady", status.prootReady);
        result.put("serverInstalled", status.serverInstalled);
        result.put("modelInstalled", status.modelInstalled);
        result.put("serverRunning", status.serverRunning);
        result.put("state", status.state);
        result.put("lastError", status.lastError);
        result.put("llamaVersion", status.llamaVersion);
        result.put("modelName", status.modelName);
        result.put("serverPort", status.serverPort);
        call.resolve(result);
    }

    @PluginMethod
    public void installLlamaServer(PluginCall call) {
        executor.submit(() -> {
            try {
                serverManager.installLlamaServer((stage, message, downloaded, total) -> {
                    notifyLlmProgress("server", stage, message, downloaded, total);
                });
                JSObject result = new JSObject();
                result.put("installed", true);
                call.resolve(result);
            } catch (Exception e) {
                notifyLlmProgress("server", "error", e.getMessage(), -1, -1);
                call.reject("Server installation failed: " + e.getMessage());
            }
        });
    }

    @PluginMethod
    public void installModel(PluginCall call) {
        executor.submit(() -> {
            try {
                String modelName = call.getString("modelName");
                String modelUrl = call.getString("modelUrl");
                String modelSha256 = call.getString("modelSha256");
                serverManager.installModel(modelName, modelUrl, modelSha256, (stage, message, downloaded, total) -> {
                    notifyLlmProgress("model", stage, message, downloaded, total);
                });
                JSObject result = new JSObject();
                result.put("installed", true);
                call.resolve(result);
            } catch (Exception e) {
                notifyLlmProgress("model", "error", e.getMessage(), -1, -1);
                call.reject("Model installation failed: " + e.getMessage());
            }
        });
    }

    @PluginMethod
    public void startLlmServer(PluginCall call) {
        executor.submit(() -> {
            try {
                serverManager.startServer();
                JSObject result = new JSObject();
                result.put("running", true);
                result.put("port", 8081);
                call.resolve(result);
            } catch (Exception e) {
                call.reject("Server start failed: " + e.getMessage());
            }
        });
    }

    @PluginMethod
    public void stopLlmServer(PluginCall call) {
        serverManager.stopServer();
        JSObject result = new JSObject();
        result.put("stopped", true);
        call.resolve(result);
    }

    @PluginMethod
    public void getLlmServerHealth(PluginCall call) {
        executor.submit(() -> {
            try {
                String body = serverManager.checkHealth();
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("response", body);
                call.resolve(result);
            } catch (Exception e) {
                JSObject result = new JSObject();
                result.put("ok", false);
                result.put("error", e.getMessage());
                call.resolve(result);
            }
        });
    }

    @PluginMethod
    public void listInstalledModels(PluginCall call) {
        List<String> models = serverManager.listInstalledModels();
        JSArray items = new JSArray();
        for (String model : models) {
            items.put(model);
        }
        JSObject result = new JSObject();
        result.put("activeModel", serverManager.getActiveModelName());
        result.put("models", items);
        call.resolve(result);
    }

    @PluginMethod
    public void setActiveModel(PluginCall call) {
        executor.submit(() -> {
            try {
                String modelName = call.getString("modelName");
                if (modelName == null || modelName.isBlank()) {
                    call.reject("modelName is required.");
                    return;
                }
                serverManager.setActiveModel(modelName);
                JSObject result = new JSObject();
                result.put("activeModel", serverManager.getActiveModelName());
                call.resolve(result);
            } catch (Exception e) {
                call.reject("Active model switch failed: " + e.getMessage());
            }
        });
    }

    // ── Progress events ─────────────────────────────────────────────────

    private void notifyLlmProgress(String component, String stage, String message,
                                    long downloadedBytes, long totalBytes) {
        JSObject event = new JSObject();
        event.put("component", component);
        event.put("stage", stage);
        event.put("message", message);
        event.put("downloadedBytes", downloadedBytes);
        event.put("totalBytes", totalBytes);
        if (totalBytes > 0 && downloadedBytes >= 0) {
            event.put("progress", Math.min(1.0, (double) downloadedBytes / (double) totalBytes));
        } else {
            event.put("progress", -1);
        }
        notifyListeners("llmInstallProgress", event);
    }

    // ── Native JNI declarations ─────────────────────────────────────────

    private static native boolean nativeLoadModel(String modelPath, int threads, int contextSize);

    private static native String nativeGenerate(String prompt, int maxTokens);

    private static native void nativeStopGeneration();

    private static native void nativeUnloadModel();
}

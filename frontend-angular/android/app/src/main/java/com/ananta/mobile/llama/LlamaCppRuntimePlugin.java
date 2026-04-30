package com.ananta.mobile.llama;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;

@CapacitorPlugin(name = "LlamaCppRuntime")
public class LlamaCppRuntimePlugin extends Plugin {
    private static volatile boolean nativeAvailable;

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

    @PluginMethod
    public void health(PluginCall call) {
        JSObject result = new JSObject();
        result.put("nativeAvailable", nativeAvailable);
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

    private static native boolean nativeLoadModel(String modelPath, int threads, int contextSize);

    private static native String nativeGenerate(String prompt, int maxTokens);

    private static native void nativeStopGeneration();

    private static native void nativeUnloadModel();
}

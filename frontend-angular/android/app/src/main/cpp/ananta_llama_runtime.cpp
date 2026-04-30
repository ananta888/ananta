#include <jni.h>
#include <atomic>
#include <string>

namespace {
std::atomic<bool> g_loaded(false);
std::atomic<bool> g_stop_requested(false);
std::string g_model_path;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_ananta_mobile_llama_LlamaCppRuntimePlugin_nativeLoadModel(
        JNIEnv* env,
        jclass,
        jstring modelPath,
        jint,
        jint) {
    const char* path = env->GetStringUTFChars(modelPath, nullptr);
    if (path == nullptr) {
        return JNI_FALSE;
    }
    g_model_path = path;
    env->ReleaseStringUTFChars(modelPath, path);
    g_stop_requested.store(false);
    g_loaded.store(!g_model_path.empty());
    return g_loaded.load() ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_ananta_mobile_llama_LlamaCppRuntimePlugin_nativeGenerate(
        JNIEnv* env,
        jclass,
        jstring prompt,
        jint maxTokens) {
    const char* input = env->GetStringUTFChars(prompt, nullptr);
    std::string promptText = input == nullptr ? "" : std::string(input);
    if (input != nullptr) {
        env->ReleaseStringUTFChars(prompt, input);
    }

    if (!g_loaded.load()) {
        return env->NewStringUTF("[llama-runtime] model not loaded");
    }
    if (g_stop_requested.load()) {
        return env->NewStringUTF("");
    }

    std::string out = "[llama-runtime-stub] prompt=" + promptText + " maxTokens=" + std::to_string(maxTokens);
    return env->NewStringUTF(out.c_str());
}

extern "C" JNIEXPORT void JNICALL
Java_com_ananta_mobile_llama_LlamaCppRuntimePlugin_nativeStopGeneration(
        JNIEnv*,
        jclass) {
    g_stop_requested.store(true);
}

extern "C" JNIEXPORT void JNICALL
Java_com_ananta_mobile_llama_LlamaCppRuntimePlugin_nativeUnloadModel(
        JNIEnv*,
        jclass) {
    g_stop_requested.store(false);
    g_loaded.store(false);
    g_model_path.clear();
}

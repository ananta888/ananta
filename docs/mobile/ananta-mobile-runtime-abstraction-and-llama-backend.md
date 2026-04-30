# Mobile Runtime Abstraction and llama.cpp Backend Scaffold

Date: 2026-04-30
Scope: ANM-010 bis ANM-027

## Runtime Abstraction (Phase 02)

Implemented Java interfaces and value objects in:

- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/ModelProvider.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/TextGenerationProvider.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/SpeechProvider.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/EmbeddingProvider.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/ProviderCapability.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/ProviderConfig.java`
- `frontend-angular/android/app/src/main/java/com/ananta/mobile/runtime/ProviderType.java`

Covered concerns:

- einheitliches Provider-Interface
- dedizierte Rollen fuer Text/Speech/Embedding
- Capability-Metadaten inkl. `maxContextTokens` und `streaming`
- Konfiguration fuer Threads, Context Size, Temperatur, Top-P und GPU-Praferenz

## llama.cpp Backend Scaffold (Phase 03)

Implemented plugin and native bridge scaffold:

- Plugin: `frontend-angular/android/app/src/main/java/com/ananta/mobile/llama/LlamaCppRuntimePlugin.java`
- Native build: `frontend-angular/android/app/src/main/cpp/CMakeLists.txt`
- JNI implementation stub: `frontend-angular/android/app/src/main/cpp/ananta_llama_runtime.cpp`
- Plugin registration: `frontend-angular/android/app/src/main/java/com/ananta/mobile/MainActivity.java`
- Optional Gradle flag: `anantaEnableLlamaCppRuntime=true`

Supported runtime API surface:

- `loadModel(modelPath, threads, contextSize)`
- `generate(prompt, maxTokens)`
- `stopGeneration()`
- `unloadModel()`
- `health()`

## Important note

Current native implementation is intentionally a safe stub and does not yet link full upstream `llama.cpp` inference kernels.
This keeps the APK build and plugin contract stable while enabling incremental integration.

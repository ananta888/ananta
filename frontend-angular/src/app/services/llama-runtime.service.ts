import { Injectable } from '@angular/core';
import { Capacitor, registerPlugin, PluginListenerHandle } from '@capacitor/core';

export interface LlmSetupStatus {
  prootReady: boolean;
  serverInstalled: boolean;
  modelInstalled: boolean;
  serverRunning: boolean;
  state: string;
  lastError?: string;
  llamaVersion: string;
  modelName: string;
  serverPort: number;
}

export interface LlmInstallProgressEvent {
  component: 'server' | 'model' | string;
  stage: 'downloading' | 'verifying' | 'extracting' | 'done' | 'error' | string;
  message: string;
  downloadedBytes: number;
  totalBytes: number;
  progress: number;
}

export interface LlmModelInstallOptions {
  modelName?: string;
  modelUrl?: string;
  modelSha256?: string;
}

export interface LlmInstalledModels {
  activeModel: string;
  models: string[];
}

interface LlamaRuntimePlugin {
  health(): Promise<{
    nativeAvailable: boolean;
    serverInstalled?: boolean;
    modelInstalled?: boolean;
    serverRunning?: boolean;
  }>;
  loadModel(options: { modelPath: string; threads?: number; contextSize?: number }): Promise<{ ok: boolean; modelPath: string; threads: number; contextSize: number }>;
  generate(options: { prompt: string; maxTokens?: number }): Promise<{ text: string }>;
  stopGeneration(): Promise<{ stopped: boolean }>;
  unloadModel(): Promise<{ unloaded: boolean }>;

  // Server-based LLM management
  getLlmSetupStatus(): Promise<LlmSetupStatus>;
  installLlamaServer(): Promise<{ installed: boolean }>;
  installModel(options?: LlmModelInstallOptions): Promise<{ installed: boolean }>;
  startLlmServer(): Promise<{ running: boolean; port: number }>;
  stopLlmServer(): Promise<{ stopped: boolean }>;
  getLlmServerHealth(): Promise<{ ok: boolean; response?: string; error?: string }>;
  listInstalledModels(): Promise<LlmInstalledModels>;
  setActiveModel(options: { modelName: string }): Promise<{ activeModel: string }>;

  addListener(
    eventName: 'llmInstallProgress',
    listenerFunc: (event: LlmInstallProgressEvent) => void
  ): Promise<PluginListenerHandle>;
}

const LlamaCppRuntime = registerPlugin<LlamaRuntimePlugin>('LlamaCppRuntime');

@Injectable({ providedIn: 'root' })
export class LlamaRuntimeService {
  readonly isNative = Capacitor.isNativePlatform();

  // ── Legacy JNI methods ──────────────────────────────────────────────

  async health(): Promise<{ nativeAvailable: boolean; serverInstalled?: boolean; modelInstalled?: boolean; serverRunning?: boolean }> {
    if (!this.isNative) return { nativeAvailable: false };
    return LlamaCppRuntime.health();
  }

  async loadModel(modelPath: string, threads = 2, contextSize = 2048): Promise<{ ok: boolean; modelPath: string; threads: number; contextSize: number }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.loadModel({ modelPath, threads, contextSize });
  }

  async generate(prompt: string, maxTokens = 128): Promise<{ text: string }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.generate({ prompt, maxTokens });
  }

  async stopGeneration(): Promise<{ stopped: boolean }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.stopGeneration();
  }

  async unloadModel(): Promise<{ unloaded: boolean }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.unloadModel();
  }

  // ── Server-based LLM management ────────────────────────────────────

  async getLlmSetupStatus(): Promise<LlmSetupStatus> {
    if (!this.isNative) {
      return {
        prootReady: false, serverInstalled: false, modelInstalled: false,
        serverRunning: false, state: 'IDLE', llamaVersion: '', modelName: '', serverPort: 0,
      };
    }
    return LlamaCppRuntime.getLlmSetupStatus();
  }

  async installLlamaServer(): Promise<{ installed: boolean }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.installLlamaServer();
  }

  async installModel(options?: LlmModelInstallOptions): Promise<{ installed: boolean }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.installModel(options ?? {});
  }

  async startLlmServer(): Promise<{ running: boolean; port: number }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.startLlmServer();
  }

  async stopLlmServer(): Promise<{ stopped: boolean }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.stopLlmServer();
  }

  async getLlmServerHealth(): Promise<{ ok: boolean; response?: string; error?: string }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.getLlmServerHealth();
  }

  async listInstalledModels(): Promise<LlmInstalledModels> {
    if (!this.isNative) return { activeModel: '', models: [] };
    return LlamaCppRuntime.listInstalledModels();
  }

  async setActiveModel(modelName: string): Promise<{ activeModel: string }> {
    if (!this.isNative) throw new Error('Nur auf nativer Android-App verfuegbar.');
    return LlamaCppRuntime.setActiveModel({ modelName });
  }

  async onLlmInstallProgress(
    listener: (event: LlmInstallProgressEvent) => void
  ): Promise<() => Promise<void>> {
    if (!this.isNative) return async () => undefined;
    const handle = await LlamaCppRuntime.addListener('llmInstallProgress', listener);
    return async () => { await handle.remove(); };
  }
}

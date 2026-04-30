import { Injectable } from '@angular/core';
import { Capacitor, registerPlugin } from '@capacitor/core';

interface LlamaRuntimePlugin {
  health(): Promise<{ nativeAvailable: boolean }>;
  loadModel(options: { modelPath: string; threads?: number; contextSize?: number }): Promise<{ ok: boolean; modelPath: string; threads: number; contextSize: number }>;
  generate(options: { prompt: string; maxTokens?: number }): Promise<{ text: string }>;
  stopGeneration(): Promise<{ stopped: boolean }>;
  unloadModel(): Promise<{ unloaded: boolean }>;
}

const LlamaCppRuntime = registerPlugin<LlamaRuntimePlugin>('LlamaCppRuntime');

@Injectable({ providedIn: 'root' })
export class LlamaRuntimeService {
  readonly isNative = Capacitor.isNativePlatform();

  async health(): Promise<{ nativeAvailable: boolean }> {
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
}

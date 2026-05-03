import { Injectable } from '@angular/core';
import { Capacitor, registerPlugin, PluginListenerHandle } from '@capacitor/core';

export interface VoxtralStatus {
  isNative: boolean;
  isRecording: boolean;
  isLiveRunning?: boolean;
  microphonePermission: string;
  audioPath?: string;
  modelPath?: string;
  runnerPath?: string;
}

export interface VoxtralLocalAsset {
  name: string;
  path: string;
  bytes: number;
  executable?: boolean;
}

interface VoxtralOfflinePlugin {
  getStatus(): Promise<VoxtralStatus>;
  requestMicrophonePermission(): Promise<{ state: string }>;
  startRecording(options?: { maxSeconds?: number; sampleRate?: number }): Promise<{ audioPath: string; maxSeconds: number; sampleRate: number }>;
  stopRecording(): Promise<{ audioPath: string }>;
  downloadModel(options: { modelUrl: string; fileName?: string; sha256?: string; minBytes?: number; confirmed?: boolean }): Promise<{ modelPath: string; bytes: number; sha256: string }>;
  downloadRunner(options: { runnerUrl: string; fileName?: string; sha256?: string; confirmed?: boolean }): Promise<{ runnerPath: string; bytes: number; sha256: string }>;
  provisionVoxtralRunner(options: { sourceUrl?: string; sourceSha256?: string; ggmlSourceUrl?: string; confirmed?: boolean }): Promise<{ runnerPath: string; binaryPath: string; sourceArchivePath: string; sourceBytes: number; sourceSha256: string; rawOutput: string }>;
  listLocalAssets(): Promise<{ models: VoxtralLocalAsset[]; runners: VoxtralLocalAsset[] }>;
  verifySetup(options: { modelPath: string; runnerPath: string; minFreeBytes?: number }): Promise<{ availableBytes: number; hasEnoughStorage: boolean; modelExists: boolean; modelBytes: number; modelCompatible?: boolean; estimatedRequiredBytes?: number; runnerExists: boolean; runnerExecutable: boolean; runnerCompatible?: boolean; runnerModelCompatible?: boolean; runnerProbeMessage?: string }>;
  getFileSha256(options: { path: string }): Promise<{ path: string; bytes: number; sha256: string }>;
  deleteAsset(options: { path: string; confirmed?: boolean }): Promise<{ path: string; deleted: boolean }>;
  transcribe(options: { audioPath: string; modelPath: string; runnerPath: string; lowMemoryMode?: boolean; confirmed?: boolean }): Promise<{ transcript: string; rawOutput: string; exitCode: number }>;
  startLiveTranscription(options: { modelPath: string; runnerPath: string; chunkSeconds?: number; sampleRate?: number; lowMemoryMode?: boolean; confirmed?: boolean }): Promise<{ started: boolean; chunkSeconds: number; sampleRate: number; lowMemoryMode?: boolean }>;
  stopLiveTranscription(): Promise<{ transcript: string }>;
  clearLastAudio(): Promise<void>;
  addListener(eventName: 'voxtralLivePartial', listener: (data: { partial: string; transcript: string; chunkPath: string }) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'voxtralLiveFinal', listener: (data: { transcript: string }) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'voxtralLiveError', listener: (data: { message: string }) => void): Promise<PluginListenerHandle>;
  addListener(eventName: 'voxtralDownloadProgress', listener: (data: { type: 'model' | 'runner'; downloadedBytes: number; totalBytes: number; progress: number }) => void): Promise<PluginListenerHandle>;
}

const VoxtralOffline = registerPlugin<VoxtralOfflinePlugin>('VoxtralOffline');

@Injectable({ providedIn: 'root' })
export class VoxtralOfflineService {
  readonly isNative = Capacitor.isNativePlatform();

  async getStatus(): Promise<VoxtralStatus> {
    if (!this.isNative) {
      return {
        isNative: false,
        isRecording: false,
        microphonePermission: 'prompt',
      };
    }
    return VoxtralOffline.getStatus();
  }

  async requestMicrophonePermission(): Promise<string> {
    if (!this.isNative) return 'denied';
    const result = await VoxtralOffline.requestMicrophonePermission();
    return result.state;
  }

  async startRecording(maxSeconds = 5, sampleRate = 16000): Promise<{ audioPath: string; maxSeconds: number; sampleRate: number }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.startRecording({ maxSeconds, sampleRate });
  }

  async stopRecording(): Promise<{ audioPath: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.stopRecording();
  }

  async downloadModel(modelUrl: string, fileName?: string, sha256?: string, minBytes?: number): Promise<{ modelPath: string; bytes: number; sha256: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.downloadModel({ modelUrl, fileName, sha256, minBytes, confirmed: true });
  }

  async downloadRunner(runnerUrl: string, fileName?: string, sha256?: string): Promise<{ runnerPath: string; bytes: number; sha256: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.downloadRunner({ runnerUrl, fileName, sha256, confirmed: true });
  }

  async provisionVoxtralRunner(sourceUrl?: string, sourceSha256?: string, ggmlSourceUrl?: string): Promise<{ runnerPath: string; binaryPath: string; sourceArchivePath: string; sourceBytes: number; sourceSha256: string; rawOutput: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.provisionVoxtralRunner({ sourceUrl, sourceSha256, ggmlSourceUrl, confirmed: true });
  }

  async listLocalAssets(): Promise<{ models: VoxtralLocalAsset[]; runners: VoxtralLocalAsset[] }> {
    if (!this.isNative) {
      return { models: [], runners: [] };
    }
    return VoxtralOffline.listLocalAssets();
  }

  async verifySetup(modelPath: string, runnerPath: string, minFreeBytes = 512 * 1024 * 1024): Promise<{ availableBytes: number; hasEnoughStorage: boolean; modelExists: boolean; modelBytes: number; modelCompatible?: boolean; estimatedRequiredBytes?: number; runnerExists: boolean; runnerExecutable: boolean; runnerCompatible?: boolean; runnerModelCompatible?: boolean; runnerProbeMessage?: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.verifySetup({ modelPath, runnerPath, minFreeBytes });
  }

  async getFileSha256(path: string): Promise<{ path: string; bytes: number; sha256: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.getFileSha256({ path });
  }

  async deleteAsset(path: string): Promise<{ path: string; deleted: boolean }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.deleteAsset({ path, confirmed: true });
  }

  async transcribe(audioPath: string, modelPath: string, runnerPath: string, lowMemoryMode = false): Promise<{ transcript: string; rawOutput: string; exitCode: number }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.transcribe({ audioPath, modelPath, runnerPath, lowMemoryMode, confirmed: true });
  }

  async startLiveTranscription(modelPath: string, runnerPath: string, chunkSeconds = 3, sampleRate = 16000, lowMemoryMode = false): Promise<{ started: boolean; chunkSeconds: number; sampleRate: number; lowMemoryMode?: boolean }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.startLiveTranscription({ modelPath, runnerPath, chunkSeconds, sampleRate, lowMemoryMode, confirmed: true });
  }

  async stopLiveTranscription(): Promise<{ transcript: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.stopLiveTranscription();
  }

  async onLivePartial(listener: (data: { partial: string; transcript: string; chunkPath: string }) => void): Promise<PluginListenerHandle> {
    return VoxtralOffline.addListener('voxtralLivePartial', listener);
  }

  async onLiveFinal(listener: (data: { transcript: string }) => void): Promise<PluginListenerHandle> {
    return VoxtralOffline.addListener('voxtralLiveFinal', listener);
  }

  async onLiveError(listener: (data: { message: string }) => void): Promise<PluginListenerHandle> {
    return VoxtralOffline.addListener('voxtralLiveError', listener);
  }

  async onDownloadProgress(listener: (data: { type: 'model' | 'runner'; downloadedBytes: number; totalBytes: number; progress: number }) => void): Promise<PluginListenerHandle> {
    return VoxtralOffline.addListener('voxtralDownloadProgress', listener);
  }

  async clearLastAudio(): Promise<void> {
    if (!this.isNative) return;
    await VoxtralOffline.clearLastAudio();
  }
}

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
  downloadModel(options: { modelUrl: string; fileName?: string; sha256?: string }): Promise<{ modelPath: string; bytes: number; sha256: string }>;
  downloadRunner(options: { runnerUrl: string; fileName?: string; sha256?: string }): Promise<{ runnerPath: string; bytes: number; sha256: string }>;
  listLocalAssets(): Promise<{ models: VoxtralLocalAsset[]; runners: VoxtralLocalAsset[] }>;
  verifySetup(options: { modelPath: string; runnerPath: string; minFreeBytes?: number }): Promise<{ availableBytes: number; hasEnoughStorage: boolean; modelExists: boolean; modelBytes: number; runnerExists: boolean; runnerExecutable: boolean }>;
  transcribe(options: { audioPath: string; modelPath: string; runnerPath: string }): Promise<{ transcript: string; rawOutput: string; exitCode: number }>;
  startLiveTranscription(options: { modelPath: string; runnerPath: string; chunkSeconds?: number; sampleRate?: number }): Promise<{ started: boolean; chunkSeconds: number; sampleRate: number }>;
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

  async downloadModel(modelUrl: string, fileName?: string, sha256?: string): Promise<{ modelPath: string; bytes: number; sha256: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.downloadModel({ modelUrl, fileName, sha256 });
  }

  async downloadRunner(runnerUrl: string, fileName?: string, sha256?: string): Promise<{ runnerPath: string; bytes: number; sha256: string }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.downloadRunner({ runnerUrl, fileName, sha256 });
  }

  async listLocalAssets(): Promise<{ models: VoxtralLocalAsset[]; runners: VoxtralLocalAsset[] }> {
    if (!this.isNative) {
      return { models: [], runners: [] };
    }
    return VoxtralOffline.listLocalAssets();
  }

  async verifySetup(modelPath: string, runnerPath: string, minFreeBytes = 512 * 1024 * 1024): Promise<{ availableBytes: number; hasEnoughStorage: boolean; modelExists: boolean; modelBytes: number; runnerExists: boolean; runnerExecutable: boolean }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.verifySetup({ modelPath, runnerPath, minFreeBytes });
  }

  async transcribe(audioPath: string, modelPath: string, runnerPath: string): Promise<{ transcript: string; rawOutput: string; exitCode: number }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.transcribe({ audioPath, modelPath, runnerPath });
  }

  async startLiveTranscription(modelPath: string, runnerPath: string, chunkSeconds = 3, sampleRate = 16000): Promise<{ started: boolean; chunkSeconds: number; sampleRate: number }> {
    if (!this.isNative) {
      throw new Error('Nur auf nativer Android-App verfuegbar.');
    }
    return VoxtralOffline.startLiveTranscription({ modelPath, runnerPath, chunkSeconds, sampleRate });
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

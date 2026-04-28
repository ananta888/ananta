import { Injectable } from '@angular/core';
import { Capacitor, registerPlugin } from '@capacitor/core';

export interface PythonRuntimeStatus {
  pythonAvailable: boolean;
  hubRunning: boolean;
  workerRunning: boolean;
  lastError?: string;
}

interface PythonRuntimePlugin {
  getRuntimeStatus(): Promise<PythonRuntimeStatus>;
  startHub(): Promise<{ hubRunning: boolean }>;
  stopHub(): Promise<{ hubRunning: boolean }>;
  startWorker(): Promise<{ workerRunning: boolean }>;
  stopWorker(): Promise<{ workerRunning: boolean }>;
  runHealthCheck(): Promise<{ ok: boolean; message: string }>;
}

const PythonRuntime = registerPlugin<PythonRuntimePlugin>('PythonRuntime');

@Injectable({ providedIn: 'root' })
export class PythonRuntimeService {
  readonly isNative = Capacitor.isNativePlatform();

  async getRuntimeStatus(): Promise<PythonRuntimeStatus> {
    if (!this.isNative) {
      return {
        pythonAvailable: false,
        hubRunning: false,
        workerRunning: false,
      };
    }
    return PythonRuntime.getRuntimeStatus();
  }

  async startHub(): Promise<{ hubRunning: boolean }> {
    return PythonRuntime.startHub();
  }

  async stopHub(): Promise<{ hubRunning: boolean }> {
    return PythonRuntime.stopHub();
  }

  async startWorker(): Promise<{ workerRunning: boolean }> {
    return PythonRuntime.startWorker();
  }

  async stopWorker(): Promise<{ workerRunning: boolean }> {
    return PythonRuntime.stopWorker();
  }

  async runHealthCheck(): Promise<{ ok: boolean; message: string }> {
    return PythonRuntime.runHealthCheck();
  }

  async ensureEmbeddedControlPlane(): Promise<PythonRuntimeStatus> {
    if (!this.isNative) return this.getRuntimeStatus();
    let status = await this.getRuntimeStatus();
    if (!status.pythonAvailable) return status;

    for (let attempt = 1; attempt <= 5; attempt += 1) {
      if (!status.hubRunning) await this.startHub();
      await this.sleep(250 * attempt);
      status = await this.getRuntimeStatus();
      if (status.hubRunning) return status;
    }

    throw new Error(status.lastError || 'Embedded hub did not reach running state.');
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

import { Injectable } from '@angular/core';
import { Capacitor, registerPlugin } from '@capacitor/core';

export interface PythonRuntimeStatus {
  pythonAvailable: boolean;
  hubRunning: boolean;
  workerRunning: boolean;
  lastError?: string;
}

export interface ShellCommandResult {
  output: string;
  exitCode: number;
  timedOut: boolean;
}

export interface ShellSessionOpenResult {
  sessionId: string;
  running: boolean;
}

export interface ShellSessionReadResult {
  output: string;
  hasMore: boolean;
  running: boolean;
  exitCode?: number;
}

interface PythonRuntimePlugin {
  getRuntimeStatus(): Promise<PythonRuntimeStatus>;
  startHub(): Promise<{ hubRunning: boolean }>;
  stopHub(): Promise<{ hubRunning: boolean }>;
  startWorker(): Promise<{ workerRunning: boolean }>;
  stopWorker(): Promise<{ workerRunning: boolean }>;
  runHealthCheck(): Promise<{ ok: boolean; message: string }>;
  runShellCommand(options: { command: string; timeoutSeconds?: number }): Promise<ShellCommandResult>;
  openShellSession(options?: { cwd?: string; initialCommand?: string; shell?: string }): Promise<ShellSessionOpenResult>;
  writeShellSession(options: { sessionId: string; input: string }): Promise<{ ok: boolean; running: boolean }>;
  readShellSession(options: { sessionId: string; maxChars?: number }): Promise<ShellSessionReadResult>;
  closeShellSession(options: { sessionId: string }): Promise<{ closed: boolean }>;
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

  async runShellCommand(command: string, timeoutSeconds = 20): Promise<ShellCommandResult> {
    if (!this.isNative) {
      throw new Error('Mobile shell ist nur in der nativen App verfuegbar.');
    }
    const normalizedCommand = String(command || '').trim();
    if (!normalizedCommand) {
      throw new Error('Bitte einen Befehl eingeben.');
    }
    return PythonRuntime.runShellCommand({ command: normalizedCommand, timeoutSeconds });
  }

  async openShellSession(options?: { cwd?: string; initialCommand?: string; shell?: string }): Promise<ShellSessionOpenResult> {
    if (!this.isNative) {
      throw new Error('Mobile shell ist nur in der nativen App verfuegbar.');
    }
    return PythonRuntime.openShellSession(options ?? {});
  }

  async writeShellSession(sessionId: string, input: string): Promise<{ ok: boolean; running: boolean }> {
    if (!this.isNative) {
      throw new Error('Mobile shell ist nur in der nativen App verfuegbar.');
    }
    const id = String(sessionId || '').trim();
    if (!id) throw new Error('sessionId fehlt.');
    return PythonRuntime.writeShellSession({ sessionId: id, input: String(input || '') });
  }

  async readShellSession(sessionId: string, maxChars = 8000): Promise<ShellSessionReadResult> {
    if (!this.isNative) {
      throw new Error('Mobile shell ist nur in der nativen App verfuegbar.');
    }
    const id = String(sessionId || '').trim();
    if (!id) throw new Error('sessionId fehlt.');
    return PythonRuntime.readShellSession({ sessionId: id, maxChars });
  }

  async closeShellSession(sessionId: string): Promise<{ closed: boolean }> {
    if (!this.isNative) {
      throw new Error('Mobile shell ist nur in der nativen App verfuegbar.');
    }
    const id = String(sessionId || '').trim();
    if (!id) throw new Error('sessionId fehlt.');
    return PythonRuntime.closeShellSession({ sessionId: id });
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

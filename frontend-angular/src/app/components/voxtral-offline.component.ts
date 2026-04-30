import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PluginListenerHandle } from '@capacitor/core';

import { VOXTRAL_MODEL_PRESETS } from '../models/voxtral-catalog';
import { ToastService } from '../services/toast.service';
import { VoxtralOfflineService } from '../services/voxtral-offline.service';

@Component({
  standalone: true,
  selector: 'app-voxtral-offline',
  imports: [FormsModule],
  template: `
    <section class="card voxtral-page">
      <h2>Voxtral Offline (Android)</h2>
      <p class="muted">
        Aufnahme und Transkription direkt in der nativen Capacitor-App.
      </p>

      @if (!voxtral.isNative) {
        <div class="card card-light warning">
          Diese Seite funktioniert nur in der nativen Android-App.
        </div>
      } @else {
        <div class="grid gap-sm mt-md">
          <label class="field">
            <span>Modell-Preset</span>
            <select [(ngModel)]="selectedModelPresetId">
              @for (preset of modelPresets; track preset.id) {
                <option [value]="preset.id">{{ preset.label }} ({{ preset.sizeHint }})</option>
              }
            </select>
          </label>
          <label class="field">
            <span>Model-Pfad (lokal auf Android)</span>
            <input [(ngModel)]="modelPath" placeholder="/data/user/0/com.ananta.mobile/files/models/voxtral-mini-4b.gguf" />
          </label>
          <label class="field">
            <span>Runner-Pfad (lokal auf Android)</span>
            <input [(ngModel)]="runnerPath" placeholder="/data/user/0/com.ananta.mobile/files/bin/voxtral-cli" />
          </label>
          <label class="field">
            <span>Model-URL (Download in App)</span>
            <input [(ngModel)]="modelUrl" placeholder="https://.../voxtral-mini-4b-realtime-q4_k.gguf" />
          </label>
          <label class="field">
            <span>Runner-URL (Download in App)</span>
            <input [(ngModel)]="runnerUrl" placeholder="https://.../voxtral-cli" />
          </label>
          <label class="field">
            <span>Installiertes Modell</span>
            <select [(ngModel)]="selectedLocalModelPath">
              <option value="">-</option>
              @for (entry of localModels; track entry.path) {
                <option [value]="entry.path">{{ entry.name }} ({{ formatBytes(entry.bytes) }})</option>
              }
            </select>
          </label>
          <label class="field">
            <span>Installierter Runner</span>
            <select [(ngModel)]="selectedLocalRunnerPath">
              <option value="">-</option>
              @for (entry of localRunners; track entry.path) {
                <option [value]="entry.path">{{ entry.name }} ({{ formatBytes(entry.bytes) }})</option>
              }
            </select>
          </label>

          <label class="field">
            <span>Aufnahme-Dauer (Sekunden)</span>
            <input type="number" min="1" max="30" [(ngModel)]="maxSeconds" />
          </label>
          <label class="field">
            <span>Live-Chunk (Sekunden)</span>
            <input type="number" min="1" max="10" [(ngModel)]="liveChunkSeconds" />
          </label>
        </div>

        <div class="row gap-sm mt-md wrap">
          <button class="secondary" type="button" (click)="requestMic()">Mikrofon erlauben</button>
          <button class="secondary" type="button" (click)="applyPresetModel()" [disabled]="busy">Preset uebernehmen</button>
          <button class="secondary" type="button" (click)="applyLatestRunnerPreset()" [disabled]="busy">Runner-Preset (auto)</button>
          <button class="secondary" type="button" (click)="applyLocalSelection()" [disabled]="busy">Auswahl uebernehmen</button>
          <button class="secondary" type="button" (click)="refreshLocalAssets()" [disabled]="busy">Lokale Dateien neu laden</button>
          <button class="primary" type="button" (click)="startRecording()" [disabled]="busy || recording">Aufnahme starten</button>
          <button class="secondary" type="button" (click)="stopRecording()" [disabled]="busy || !recording">Aufnahme stoppen</button>
          <button class="secondary" type="button" (click)="downloadModel()" [disabled]="busy || !modelUrl.trim()">Model laden</button>
          <button class="secondary" type="button" (click)="downloadRunner()" [disabled]="busy || !runnerUrl.trim()">Runner laden</button>
          <button class="secondary" type="button" (click)="verifySetup()" [disabled]="busy || !modelPath.trim() || !runnerPath.trim()">Setup pruefen</button>
          <button class="primary" type="button" (click)="transcribe()" [disabled]="busy || !audioPath || !modelPath.trim() || !runnerPath.trim()">Transkribieren</button>
          <button class="primary" type="button" (click)="startLive()" [disabled]="busy || liveRunning || !modelPath.trim() || !runnerPath.trim()">Live starten</button>
          <button class="secondary" type="button" (click)="stopLive()" [disabled]="busy || !liveRunning">Live stoppen</button>
          <button class="secondary" type="button" (click)="clearAudio()" [disabled]="busy || !audioPath">Audio loeschen</button>
        </div>

        <div class="grid gap-sm mt-md">
          <div><strong>Status:</strong> {{ recording ? 'nimmt auf' : 'bereit' }}</div>
          <div><strong>Live:</strong> {{ liveRunning ? 'aktiv' : 'aus' }}</div>
          <div><strong>Permission:</strong> {{ permissionState }}</div>
          <div><strong>Audio:</strong> {{ audioPath || '-' }}</div>
          <div><strong>Model:</strong> {{ modelPath || '-' }}</div>
          <div><strong>Runner:</strong> {{ runnerPath || '-' }}</div>
          <div><strong>Download Modell:</strong> {{ modelDownloadProgress }}</div>
          <div><strong>Download Runner:</strong> {{ runnerDownloadProgress }}</div>
          <div class="muted"><small>Runner-Tipp: Mit "Runner-Preset (auto)" wird das aktuelle llama.cpp Android-Archiv gesetzt; beim Download wird ein passender Runner automatisch extrahiert.</small></div>
          <div><strong>Setup:</strong> {{ setupStatus || '-' }}</div>
        </div>

        @if (errorMessage) {
          <pre class="card card-light error-box">{{ errorMessage }}</pre>
        }
        @if (transcript) {
          <pre class="card card-light transcript-box">{{ transcript }}</pre>
        }
        @if (liveTranscript) {
          <pre class="card card-light transcript-box">{{ liveTranscript }}</pre>
        }
        @if (rawOutput) {
          <pre class="card card-light">{{ rawOutput }}</pre>
        }
      }
    </section>
  `,
  styles: [`
    .voxtral-page {
      max-width: 920px;
      margin: 0 auto;
    }
    .warning {
      border-color: #f59e0b;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .field input, .field select {
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--fg);
      border-radius: 6px;
      padding: 8px 10px;
    }
    .wrap {
      flex-wrap: wrap;
    }
    .error-box {
      border-color: #ef4444;
      color: #ef4444;
      white-space: pre-wrap;
    }
    .transcript-box {
      white-space: pre-wrap;
      line-height: 1.4;
    }
  `],
})
export class VoxtralOfflineComponent implements OnInit, OnDestroy {
  voxtral = inject(VoxtralOfflineService);
  private toast = inject(ToastService);
  modelPresets = VOXTRAL_MODEL_PRESETS;

  busy = false;
  recording = false;
  liveRunning = false;
  permissionState = 'unknown';
  modelPath = '';
  runnerPath = '';
  audioPath = '';
  selectedModelPresetId = this.modelPresets.find(p => p.recommended)?.id || this.modelPresets[0]?.id || '';
  selectedLocalModelPath = '';
  selectedLocalRunnerPath = '';
  localModels: Array<{ name: string; path: string; bytes: number }> = [];
  localRunners: Array<{ name: string; path: string; bytes: number }> = [];
  modelUrl = '';
  runnerUrl = '';
  transcript = '';
  rawOutput = '';
  liveTranscript = '';
  errorMessage = '';
  modelDownloadProgress = '-';
  runnerDownloadProgress = '-';
  setupStatus = '';
  maxSeconds = 5;
  liveChunkSeconds = 3;
  private livePartialHandle?: PluginListenerHandle;
  private liveFinalHandle?: PluginListenerHandle;
  private liveErrorHandle?: PluginListenerHandle;
  private downloadProgressHandle?: PluginListenerHandle;

  async ngOnInit(): Promise<void> {
    this.restoreSelections();
    if (this.voxtral.isNative) {
      this.livePartialHandle = await this.voxtral.onLivePartial((data) => {
        this.liveTranscript = data.transcript || this.liveTranscript;
      });
      this.liveFinalHandle = await this.voxtral.onLiveFinal((data) => {
        this.liveRunning = false;
        if (data?.transcript) this.liveTranscript = data.transcript;
      });
      this.liveErrorHandle = await this.voxtral.onLiveError((data) => {
        this.liveRunning = false;
        this.errorMessage = data?.message || 'Live-Transkription fehlgeschlagen.';
        this.toast.error(this.errorMessage);
      });
      this.downloadProgressHandle = await this.voxtral.onDownloadProgress((data) => {
        const progress = data.progress >= 0 ? `${Math.round(data.progress * 100)}%` : `${this.formatBytes(data.downloadedBytes)} geladen`;
        if (data.type === 'model') this.modelDownloadProgress = progress;
        if (data.type === 'runner') this.runnerDownloadProgress = progress;
      });
    }
    await this.refreshStatus();
    await this.refreshLocalAssets();
  }

  ngOnDestroy(): void {
    this.livePartialHandle?.remove();
    this.liveFinalHandle?.remove();
    this.liveErrorHandle?.remove();
    this.downloadProgressHandle?.remove();
  }

  async requestMic(): Promise<void> {
    await this.run(async () => {
      this.permissionState = await this.voxtral.requestMicrophonePermission();
      this.toast.info(`Mikrofon-Permission: ${this.permissionState}`);
    });
  }

  applyPresetModel(): void {
    const preset = this.modelPresets.find(item => item.id === this.selectedModelPresetId);
    if (!preset) return;
    this.modelUrl = preset.url;
    this.modelPath = this.modelPath || '';
    this.toast.info(`Preset gewaehlt: ${preset.label}`);
    this.persistSelections();
  }

  async applyLatestRunnerPreset(): Promise<void> {
    await this.run(async () => {
      const preset = await this.voxtral.resolveLatestAndroidRunnerPreset();
      this.runnerUrl = preset.url;
      this.persistSelections();
      this.toast.info(`Runner-Preset gesetzt: ${preset.label}`);
    });
  }

  applyLocalSelection(): void {
    if (this.selectedLocalModelPath) this.modelPath = this.selectedLocalModelPath;
    if (this.selectedLocalRunnerPath) this.runnerPath = this.selectedLocalRunnerPath;
    this.persistSelections();
    this.toast.info('Lokale Auswahl uebernommen.');
  }

  async startRecording(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.startRecording(this.maxSeconds);
      this.audioPath = result.audioPath;
      this.recording = true;
      this.transcript = '';
      this.rawOutput = '';
      this.toast.success(`Aufnahme gestartet (${result.maxSeconds}s).`);
    });
  }

  async stopRecording(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.stopRecording();
      this.audioPath = result.audioPath;
      this.recording = false;
      this.toast.success('Aufnahme gestoppt.');
      await this.refreshStatus();
    });
  }

  async transcribe(): Promise<void> {
    await this.run(async () => {
      if (!this.audioPath) throw new Error('Kein Audio vorhanden.');
      if (!this.modelPath.trim()) throw new Error('Bitte zuerst den Model-Pfad setzen.');
      if (!this.runnerPath.trim()) throw new Error('Bitte zuerst den Runner-Pfad setzen.');
      const result = await this.voxtral.transcribe(this.audioPath, this.modelPath.trim(), this.runnerPath.trim());
      this.transcript = result.transcript || '';
      this.rawOutput = result.rawOutput || '';
      this.liveTranscript = '';
      this.toast.success('Transkription abgeschlossen.');
    });
  }

  async startLive(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.startLiveTranscription(
        this.modelPath.trim(),
        this.runnerPath.trim(),
        this.liveChunkSeconds
      );
      this.liveRunning = !!result.started;
      this.liveTranscript = '';
      this.rawOutput = '';
      this.toast.success(`Live gestartet (Chunk ${result.chunkSeconds}s).`);
    });
  }

  async stopLive(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.stopLiveTranscription();
      this.liveRunning = false;
      this.liveTranscript = result.transcript || this.liveTranscript;
      this.toast.info('Live-Modus gestoppt.');
    });
  }

  async downloadModel(): Promise<void> {
    await this.run(async () => {
      const preset = this.modelPresets.find(item => item.id === this.selectedModelPresetId);
      const fileName = preset?.fileName;
      const result = await this.voxtral.downloadModel(this.modelUrl.trim(), fileName);
      this.modelPath = result.modelPath;
      this.modelDownloadProgress = '100%';
      this.persistSelections();
      await this.refreshLocalAssets();
      this.toast.success(`Model geladen (${this.formatBytes(result.bytes)}).`);
    });
  }

  async downloadRunner(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.downloadRunner(this.runnerUrl.trim());
      this.runnerPath = result.runnerPath;
      this.runnerDownloadProgress = '100%';
      this.persistSelections();
      await this.refreshLocalAssets();
      this.toast.success(`Runner geladen (${this.formatBytes(result.bytes)}).`);
    });
  }

  async refreshLocalAssets(): Promise<void> {
    await this.run(async () => {
      const assets = await this.voxtral.listLocalAssets();
      this.localModels = assets.models || [];
      this.localRunners = assets.runners || [];
      if (this.modelPath && !this.selectedLocalModelPath) this.selectedLocalModelPath = this.modelPath;
      if (this.runnerPath && !this.selectedLocalRunnerPath) this.selectedLocalRunnerPath = this.runnerPath;
    }, false);
  }

  async verifySetup(): Promise<void> {
    await this.run(async () => {
      const check = await this.voxtral.verifySetup(this.modelPath.trim(), this.runnerPath.trim());
      this.setupStatus = `Speicher frei: ${this.formatBytes(check.availableBytes)} | Bedarf: ${this.formatBytes(check.estimatedRequiredBytes || 0)} | Modell: ${check.modelExists && check.modelCompatible ? 'ok' : 'inkompatibel/fehlt'} | Runner: ${check.runnerExecutable && check.runnerCompatible ? 'ok' : 'inkompatibel/nicht ausfuehrbar'}`;
      if (!check.modelExists || !check.runnerExecutable || !check.modelCompatible || !check.runnerCompatible || !check.hasEnoughStorage) {
        throw new Error('Setup unvollstaendig. Bitte Modell/Runner pruefen.');
      }
      this.toast.success('Setup ist bereit.');
    });
  }

  async clearAudio(): Promise<void> {
    await this.run(async () => {
      await this.voxtral.clearLastAudio();
      this.audioPath = '';
      this.recording = false;
      this.rawOutput = '';
      this.liveTranscript = '';
      this.setupStatus = '';
      this.toast.info('Audio geloescht.');
    });
  }

  private async refreshStatus(): Promise<void> {
    await this.run(async () => {
      const status = await this.voxtral.getStatus();
      this.recording = !!status.isRecording;
      this.liveRunning = !!status.isLiveRunning;
      this.permissionState = status.microphonePermission;
      if (status.audioPath) this.audioPath = status.audioPath;
      if (status.modelPath) this.modelPath = status.modelPath;
      if (status.runnerPath) this.runnerPath = status.runnerPath;
      this.persistSelections();
    }, false);
  }

  private restoreSelections(): void {
    this.modelPath = localStorage.getItem('voxtral.modelPath') || '';
    this.runnerPath = localStorage.getItem('voxtral.runnerPath') || '';
    this.modelUrl = localStorage.getItem('voxtral.modelUrl') || this.modelUrl;
    this.runnerUrl = localStorage.getItem('voxtral.runnerUrl') || '';
    this.selectedModelPresetId = localStorage.getItem('voxtral.modelPresetId') || this.selectedModelPresetId;
    if (!this.modelUrl) {
      const preset = this.modelPresets.find(item => item.id === this.selectedModelPresetId);
      if (preset) this.modelUrl = preset.url;
    }
  }

  private persistSelections(): void {
    localStorage.setItem('voxtral.modelPath', this.modelPath || '');
    localStorage.setItem('voxtral.runnerPath', this.runnerPath || '');
    localStorage.setItem('voxtral.modelUrl', this.modelUrl || '');
    localStorage.setItem('voxtral.runnerUrl', this.runnerUrl || '');
    localStorage.setItem('voxtral.modelPresetId', this.selectedModelPresetId || '');
  }

  formatBytes(bytes: number): string {
    if (!bytes || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
  }

  private async run(work: () => Promise<void>, showErrorToast = true): Promise<void> {
    this.busy = true;
    this.errorMessage = '';
    try {
      await work();
    } catch (error: any) {
      const message = error?.message || String(error);
      this.errorMessage = message;
      if (showErrorToast) this.toast.error(message);
    } finally {
      this.busy = false;
    }
  }
}

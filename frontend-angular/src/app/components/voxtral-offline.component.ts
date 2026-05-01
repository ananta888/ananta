import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PluginListenerHandle } from '@capacitor/core';
import { firstValueFrom } from 'rxjs';

import { VOXTRAL_MODEL_PRESETS } from '../models/voxtral-catalog';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
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
            <span>Erwartete SHA256 Modell (optional)</span>
            <input [(ngModel)]="modelExpectedSha256" placeholder="z. B. 64-stellige sha256 hex" />
          </label>
          <label class="field">
            <span>Erwartete SHA256 Runner (optional)</span>
            <input [(ngModel)]="runnerExpectedSha256" placeholder="z. B. 64-stellige sha256 hex" />
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
          <button class="secondary" type="button" (click)="provisionVoxtralRunner()" [disabled]="busy">Voxtral-Runner automatisch bauen</button>
          <button class="secondary" type="button" (click)="verifyModelHash()" [disabled]="busy || !modelPath.trim()">Hash Modell pruefen</button>
          <button class="secondary" type="button" (click)="verifyRunnerHash()" [disabled]="busy || !runnerPath.trim()">Hash Runner pruefen</button>
          <button class="secondary" type="button" (click)="deleteModel()" [disabled]="busy || !modelPath.trim()">Modell loeschen</button>
          <button class="secondary" type="button" (click)="deleteRunner()" [disabled]="busy || !runnerPath.trim()">Runner loeschen</button>
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
          <div><strong>SHA256 Modell:</strong> {{ modelSha256 || '-' }}</div>
          <div><strong>SHA256 Runner:</strong> {{ runnerSha256 || '-' }}</div>
          <div><strong>Lokale Assets:</strong> Modell {{ localModelAvailable ? 'vorhanden' : 'fehlt' }} | Runner {{ localRunnerAvailable ? 'vorhanden' : 'fehlt' }}</div>
          @if (localModelAvailable && localRunnerAvailable) {
            <div class="muted"><small>Lokale Dateien erkannt: Kein erneuter Download noetig.</small></div>
          }
          <div class="muted"><small>Runner-Tipp: Fuer Voxtral wird ein Voxtral-Runner benoetigt (z. B. voxtral-cli/voxtral4b-main im Ordner .../files/voxtral/bin). Nutze am besten "Voxtral-Runner automatisch bauen". Falls apt/dpkg in Proot blockiert ist, Runner direkt als Datei in .../files/voxtral/bin bereitstellen.</small></div>
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
        <div class="card card-light mt-sm">
          <strong>Ananta Goal aus Transkript starten</strong>
          <div class="muted mt-sm">
            Nutzt den Hub-Endpoint direkt in der App und erstellt daraus Aufgaben.
          </div>
          <label class="field mt-sm">
            <span>Kontext (optional)</span>
            <input [(ngModel)]="goalContext" placeholder="z. B. Erstelle konkrete naechste Schritte fuer dieses Sprachziel" />
          </label>
          <div class="row gap-sm mt-sm wrap">
            <button
              class="primary"
              type="button"
              (click)="startGoalFromTranscript()"
              [disabled]="busy || goalBusy || !effectiveTranscript.trim()">
              {{ goalBusy ? 'Starte Goal...' : 'Transkript als Goal starten' }}
            </button>
          </div>
          <div class="muted mt-sm"><strong>Aktives Transkript:</strong> {{ effectiveTranscript || '-' }}</div>
          @if (goalResult) {
            <pre class="card card-light mt-sm transcript-box">{{ goalResult }}</pre>
          }
          @if (goalError) {
            <pre class="card card-light error-box mt-sm">{{ goalError }}</pre>
          }
        </div>
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
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);
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
  modelExpectedSha256 = '';
  runnerExpectedSha256 = '';
  modelSha256 = '';
  runnerSha256 = '';
  transcript = '';
  rawOutput = '';
  liveTranscript = '';
  errorMessage = '';
  modelDownloadProgress = '-';
  runnerDownloadProgress = '-';
  localModelAvailable = false;
  localRunnerAvailable = false;
  private modelDownloadActive = false;
  private runnerDownloadActive = false;
  setupStatus = '';
  maxSeconds = 5;
  liveChunkSeconds = 3;
  goalContext = 'Voxtral Sprachtranskript';
  goalBusy = false;
  goalResult = '';
  goalError = '';
  private livePartialHandle?: PluginListenerHandle;
  private liveFinalHandle?: PluginListenerHandle;
  private liveErrorHandle?: PluginListenerHandle;
  private downloadProgressHandle?: PluginListenerHandle;

  get effectiveTranscript(): string {
    return String(this.transcript || this.liveTranscript || '').trim();
  }

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
        if (data.type === 'model') {
          this.modelDownloadActive = true;
          this.modelDownloadProgress = progress;
        }
        if (data.type === 'runner') {
          this.runnerDownloadActive = true;
          this.runnerDownloadProgress = progress;
        }
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

  async startGoalFromTranscript(): Promise<void> {
    if (this.goalBusy) return;
    const goalText = this.effectiveTranscript;
    if (!goalText) {
      this.goalError = 'Kein Transkript vorhanden.';
      return;
    }
    const hubUrl = this.resolveHubUrl();
    if (!hubUrl) {
      this.goalError = 'Kein Hub konfiguriert. Bitte Agenten-Directory pruefen.';
      return;
    }
    this.goalBusy = true;
    this.goalError = '';
    this.goalResult = '';
    try {
      const result: any = await firstValueFrom(this.hubApi.planGoal(hubUrl, {
        goal: goalText,
        context: this.goalContext?.trim() || undefined,
        create_tasks: true,
      }));
      const goalId = String(result?.goal_id || '').trim();
      const taskIds = Array.isArray(result?.created_task_ids) ? result.created_task_ids : [];
      this.goalResult = [
        `Goal gestartet${goalId ? `: ${goalId}` : ''}`,
        `Erstellte Tasks: ${taskIds.length}`,
        taskIds.length ? `Task IDs: ${taskIds.slice(0, 5).join(', ')}${taskIds.length > 5 ? ' ...' : ''}` : '',
      ].filter(Boolean).join('\n');
      this.toast.success(`Voxtral-Goal gestartet (${taskIds.length} Tasks).`);
    } catch (error: any) {
      const message = error?.error?.message || error?.message || String(error);
      this.goalError = `Goal konnte nicht gestartet werden: ${message}`;
      this.toast.error(this.goalError);
    } finally {
      this.goalBusy = false;
    }
  }

  async downloadModel(): Promise<void> {
    await this.run(async () => {
      this.modelDownloadActive = true;
      this.modelDownloadProgress = '0%';
      try {
        const preset = this.modelPresets.find(item => item.id === this.selectedModelPresetId);
        const fileName = preset?.fileName;
        const minBytes = preset?.minBytes;
        const result = await this.voxtral.downloadModel(
          this.modelUrl.trim(),
          fileName,
          this.modelExpectedSha256.trim() || undefined,
          minBytes
        );
        this.modelPath = result.modelPath;
        this.modelSha256 = result.sha256 || '';
        this.modelDownloadProgress = '100%';
        this.persistSelections();
        await this.refreshLocalAssets();
        this.toast.success(`Model geladen (${this.formatBytes(result.bytes)}).`);
      } finally {
        this.modelDownloadActive = false;
        this.updateDownloadIndicatorsFromLocalAssets();
      }
    });
  }

  async downloadRunner(): Promise<void> {
    await this.run(async () => {
      this.runnerDownloadActive = true;
      this.runnerDownloadProgress = '0%';
      try {
        const result = await this.voxtral.downloadRunner(
          this.runnerUrl.trim(),
          undefined,
          this.runnerExpectedSha256.trim() || undefined
        );
        this.runnerPath = result.runnerPath;
        this.runnerSha256 = result.sha256 || '';
        this.runnerDownloadProgress = '100%';
        this.persistSelections();
        await this.refreshLocalAssets();
        this.toast.success(`Runner geladen (${this.formatBytes(result.bytes)}).`);
      } finally {
        this.runnerDownloadActive = false;
        this.updateDownloadIndicatorsFromLocalAssets();
      }
    });
  }

  async provisionVoxtralRunner(): Promise<void> {
    await this.run(async () => {
      this.runnerDownloadActive = true;
      this.runnerDownloadProgress = '0%';
      try {
        const result = await this.voxtral.provisionVoxtralRunner();
        this.runnerPath = result.runnerPath;
        this.persistSelections();
        await this.refreshLocalAssets();
        this.toast.success('Voxtral-Runner erfolgreich bereitgestellt.');
      } finally {
        this.runnerDownloadActive = false;
        this.updateDownloadIndicatorsFromLocalAssets();
      }
    });
  }

  async refreshLocalAssets(): Promise<void> {
    await this.run(async () => {
      const assets = await this.voxtral.listLocalAssets();
      this.localModels = assets.models || [];
      this.localRunners = assets.runners || [];
      this.localModelAvailable = this.localModels.length > 0;
      this.localRunnerAvailable = this.localRunners.length > 0;
      const modelExists = !!this.modelPath && this.localModels.some(item => item.path === this.modelPath);
      const runnerExists = !!this.runnerPath && this.localRunners.some(item => item.path === this.runnerPath);
      if (!modelExists && this.localModels.length) {
        this.modelPath = this.localModels[0].path;
      }
      if (!runnerExists && this.localRunners.length) {
        const preferredRunner = this.localRunners.find(item => item.name.toLowerCase().includes('voxtral'));
        this.runnerPath = (preferredRunner || this.localRunners[0]).path;
      }
      if (this.modelPath) this.selectedLocalModelPath = this.modelPath;
      if (this.runnerPath) this.selectedLocalRunnerPath = this.runnerPath;
      this.persistSelections();
      this.updateDownloadIndicatorsFromLocalAssets();
    }, false);
  }

  async verifySetup(): Promise<void> {
    await this.run(async () => {
      const check = await this.voxtral.verifySetup(this.modelPath.trim(), this.runnerPath.trim());
      const runnerExecOk = !!check.runnerExecutable && !!check.runnerCompatible;
      const runnerModelOk = check.runnerModelCompatible !== false;
      const probeHint = String(check.runnerProbeMessage || '').trim();
      this.setupStatus = `Speicher frei: ${this.formatBytes(check.availableBytes)} | Bedarf: ${this.formatBytes(check.estimatedRequiredBytes || 0)} | Modell: ${check.modelExists && check.modelCompatible ? 'ok' : 'inkompatibel/fehlt'} | Runner: ${runnerExecOk ? 'ok' : 'inkompatibel/nicht ausfuehrbar'}${runnerModelOk ? '' : ' (modellinkompatibel)'}${probeHint ? ` | Probe: ${probeHint}` : ''}`;
      if (!check.modelExists || !runnerExecOk || !check.modelCompatible || !runnerModelOk || !check.hasEnoughStorage) {
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

  async verifyModelHash(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.getFileSha256(this.modelPath.trim());
      this.modelSha256 = result.sha256 || '';
      if (this.modelExpectedSha256.trim()) {
        const expected = this.modelExpectedSha256.trim().toLowerCase();
        const actual = this.modelSha256.toLowerCase();
        if (expected !== actual) {
          throw new Error(`Modell-SHA256 stimmt nicht. Erwartet: ${expected}, Ist: ${actual}`);
        }
      }
      this.toast.success('Modell-SHA256 erfolgreich geprueft.');
      this.persistSelections();
    });
  }

  async verifyRunnerHash(): Promise<void> {
    await this.run(async () => {
      const result = await this.voxtral.getFileSha256(this.runnerPath.trim());
      this.runnerSha256 = result.sha256 || '';
      if (this.runnerExpectedSha256.trim()) {
        const expected = this.runnerExpectedSha256.trim().toLowerCase();
        const actual = this.runnerSha256.toLowerCase();
        if (expected !== actual) {
          throw new Error(`Runner-SHA256 stimmt nicht. Erwartet: ${expected}, Ist: ${actual}`);
        }
      }
      this.toast.success('Runner-SHA256 erfolgreich geprueft.');
      this.persistSelections();
    });
  }

  async deleteModel(): Promise<void> {
    if (!this.modelPath.trim()) return;
    const confirmed = window.confirm(`Modell wirklich loeschen?\n${this.modelPath}`);
    if (!confirmed) return;
    await this.run(async () => {
      await this.voxtral.deleteAsset(this.modelPath.trim());
      this.modelPath = '';
      this.selectedLocalModelPath = '';
      this.modelSha256 = '';
      this.setupStatus = '';
      await this.refreshLocalAssets();
      this.persistSelections();
      this.toast.info('Modell geloescht.');
    });
  }

  async deleteRunner(): Promise<void> {
    if (!this.runnerPath.trim()) return;
    const confirmed = window.confirm(`Runner wirklich loeschen?\n${this.runnerPath}`);
    if (!confirmed) return;
    await this.run(async () => {
      await this.voxtral.deleteAsset(this.runnerPath.trim());
      this.runnerPath = '';
      this.selectedLocalRunnerPath = '';
      this.runnerSha256 = '';
      this.setupStatus = '';
      await this.refreshLocalAssets();
      this.persistSelections();
      this.toast.info('Runner geloescht.');
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
      this.updateDownloadIndicatorsFromLocalAssets();
    }, false);
  }

  private updateDownloadIndicatorsFromLocalAssets(): void {
    if (!this.modelDownloadActive) {
      this.modelDownloadProgress = this.localModelAvailable ? 'vorhanden' : '-';
    }
    if (!this.runnerDownloadActive) {
      this.runnerDownloadProgress = this.localRunnerAvailable ? 'vorhanden' : '-';
    }
  }

  private restoreSelections(): void {
    this.modelPath = localStorage.getItem('voxtral.modelPath') || '';
    this.runnerPath = localStorage.getItem('voxtral.runnerPath') || '';
    this.modelUrl = localStorage.getItem('voxtral.modelUrl') || this.modelUrl;
    this.runnerUrl = localStorage.getItem('voxtral.runnerUrl') || '';
    this.modelExpectedSha256 = localStorage.getItem('voxtral.modelExpectedSha256') || '';
    this.runnerExpectedSha256 = localStorage.getItem('voxtral.runnerExpectedSha256') || '';
    this.modelSha256 = localStorage.getItem('voxtral.modelSha256') || '';
    this.runnerSha256 = localStorage.getItem('voxtral.runnerSha256') || '';
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
    localStorage.setItem('voxtral.modelExpectedSha256', this.modelExpectedSha256 || '');
    localStorage.setItem('voxtral.runnerExpectedSha256', this.runnerExpectedSha256 || '');
    localStorage.setItem('voxtral.modelSha256', this.modelSha256 || '');
    localStorage.setItem('voxtral.runnerSha256', this.runnerSha256 || '');
    localStorage.setItem('voxtral.modelPresetId', this.selectedModelPresetId || '');
  }

  private resolveHubUrl(): string | null {
    const agents = this.dir.list();
    const hub = agents.find((item) => item.role === 'hub') || agents.find((item) => item.name === 'hub');
    const url = String(hub?.url || '').trim();
    return url || null;
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

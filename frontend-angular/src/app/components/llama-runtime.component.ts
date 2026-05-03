import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Capacitor } from '@capacitor/core';

import { LlamaRuntimeService, LlmInstallProgressEvent, LlmSetupStatus } from '../services/llama-runtime.service';
import { ToastService } from '../services/toast.service';

interface LlmModelPreset {
  id: string;
  label: string;
  modelName: string;
  modelUrl: string;
  modelSha256?: string;
  sizeHint: string;
}

@Component({
  standalone: true,
  selector: 'app-llama-runtime',
  imports: [FormsModule],
  template: `
    <section class="card runtime-page">
      <h2>LLM Runtime Setup</h2>
      <p class="muted">Lokale Modelle direkt auf dem Geraet installieren und den LLM-Server starten.</p>

      @if (!isAndroidNative) {
        <div class="card card-light">Nur in der nativen Android-App verfuegbar.</div>
      } @else {

        <div class="card card-light mt-sm status-grid">
          <div class="status-row">
            <span class="status-icon">{{ status.prootReady ? '✅' : '❌' }}</span>
            <span>1. Ubuntu Runtime</span>
            <span class="muted">{{ status.prootReady ? 'Bereit' : 'Nicht installiert — bitte zuerst unter Mobile Shell einrichten' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.serverInstalled ? '✅' : '❌' }}</span>
            <span>2. LLM Server (llama.cpp {{ status.llamaVersion }})</span>
            <span class="muted">{{ status.serverInstalled ? 'In APK enthalten / installiert' : 'Noch nicht installiert' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.modelInstalled ? '✅' : '❌' }}</span>
            <span>3. Aktives Modell ({{ status.modelName || 'noch keines' }})</span>
            <span class="muted">{{ status.modelInstalled ? ('Installierte Modelle: ' + installedModels.length) : 'Noch kein Modell installiert' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.serverRunning ? '🟢' : '⚪' }}</span>
            <span>4. Server-Status</span>
            <span class="muted">{{ status.serverRunning ? 'Laeuft auf Port ' + status.serverPort : 'Gestoppt' }}</span>
          </div>
        </div>

        @if (progressActive) {
          <div class="card card-light mt-sm">
            <div class="muted">{{ progressLabel }}</div>
            @if (progressPercent >= 0) {
              <progress [value]="progressPercent" max="100"></progress>
              <div class="muted progress-detail">{{ progressPercent }}%{{ progressSize ? ' — ' + progressSize : '' }}</div>
            }
          </div>
        }

        <div class="row gap-sm mt-md wrap">
          <button type="button" class="secondary" (click)="refreshStatus()" [disabled]="busy">Status aktualisieren</button>
          <button type="button" class="primary" (click)="installServer()" [disabled]="busy || !status.prootReady || status.serverInstalled">
            {{ status.serverInstalled ? 'Server installiert ✓' : 'LLM Server installieren' }}
          </button>
        </div>

        <div class="card card-light mt-sm">
          <h3 class="mt-0">Modelle (bis ~2 GB)</h3>
          <label class="field">
            <span>Preset auswaehlen</span>
            <select [(ngModel)]="selectedPresetId">
              @for (preset of modelPresets; track preset.id) {
                <option [value]="preset.id">{{ preset.label }} ({{ preset.sizeHint }})</option>
              }
              <option value="custom">Eigene Quelle (URL)</option>
            </select>
          </label>

          @if (isCustomPreset) {
            <div class="grid gap-sm mt-sm">
              <label class="field">
                <span>Model URL (.gguf)</span>
                <input [(ngModel)]="customModelUrl" placeholder="https://.../model.gguf" />
              </label>
              <label class="field">
                <span>Dateiname</span>
                <input [(ngModel)]="customModelName" placeholder="model.gguf" />
              </label>
              <label class="field">
                <span>SHA256 (optional)</span>
                <input [(ngModel)]="customModelSha256" placeholder="optional" />
              </label>
            </div>
          } @else {
            <div class="muted mt-sm">Quelle: {{ selectedPreset?.modelUrl }}</div>
          }

          <div class="row gap-sm mt-sm wrap">
            <button type="button" class="primary" (click)="installModel()" [disabled]="busy || !status.serverInstalled || !canInstallSelectedModel">
              Modell installieren / aktualisieren
            </button>
          </div>
        </div>

        <div class="card card-light mt-sm">
          <h3 class="mt-0">Installierte Modelle</h3>
          @if (installedModels.length === 0) {
            <div class="muted">Noch keine Modelle installiert.</div>
          } @else {
            <label class="field">
              <span>Aktives Modell</span>
              <select [(ngModel)]="selectedInstalledModel">
                @for (model of installedModels; track model) {
                  <option [value]="model">{{ model }}</option>
                }
              </select>
            </label>
            <div class="row gap-sm mt-sm wrap">
              <button type="button" class="secondary" (click)="activateSelectedModel()" [disabled]="busy || !selectedInstalledModel || selectedInstalledModel === status.modelName">
                Als aktiv setzen
              </button>
            </div>
          }
        </div>

        <div class="row gap-sm mt-sm wrap">
          @if (!status.serverRunning) {
            <button type="button" class="primary" (click)="startServer()" [disabled]="busy || !status.serverInstalled || !status.modelInstalled || !status.prootReady">
              Server starten
            </button>
          } @else {
            <button type="button" class="secondary" (click)="stopServer()" [disabled]="busy">Server stoppen</button>
          }
          <button type="button" class="secondary" (click)="checkHealth()" [disabled]="busy || !status.serverRunning">
            Health Check
          </button>
        </div>

        @if (errorMessage) {
          <pre class="card card-light error-box mt-sm">{{ errorMessage }}</pre>
        }
        @if (infoMessage) {
          <pre class="card card-light output-box mt-sm">{{ infoMessage }}</pre>
        }

        @if (status.serverRunning) {
          <details class="mt-md">
            <summary>Schnelltest</summary>
            <div class="grid gap-sm mt-sm">
              <label class="field">
                <span>Prompt</span>
                <textarea rows="3" [(ngModel)]="testPrompt" placeholder="z.B. Was ist die Hauptstadt von Frankreich?"></textarea>
              </label>
              <div class="row gap-sm">
                <button type="button" class="primary" (click)="testGenerate()" [disabled]="busy || !testPrompt.trim()">Generieren</button>
              </div>
              @if (testOutput) {
                <pre class="card card-light output-box">{{ testOutput }}</pre>
              }
            </div>
          </details>
        }
      }
    </section>
  `,
  styles: [`
    .runtime-page { max-width: 920px; margin: 0 auto; }
    .status-grid { display: flex; flex-direction: column; gap: 10px; }
    .status-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .status-icon { font-size: 1.2em; min-width: 24px; text-align: center; }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field input, .field textarea, .field select {
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--fg);
      border-radius: 6px;
      padding: 8px 10px;
    }
    .wrap { flex-wrap: wrap; }
    .error-box { border-color: #ef4444; color: #ef4444; white-space: pre-wrap; }
    .output-box { white-space: pre-wrap; }
    progress { width: 100%; height: 8px; }
    .progress-detail { font-size: 0.85em; }
  `],
})
export class LlamaRuntimeComponent implements OnInit, OnDestroy {
  private readonly runtime = inject(LlamaRuntimeService);
  private readonly toast = inject(ToastService);

  busy = false;
  status: LlmSetupStatus = {
    prootReady: false, serverInstalled: false, modelInstalled: false,
    serverRunning: false, state: 'IDLE', llamaVersion: '', modelName: '', serverPort: 0,
  };
  errorMessage = '';
  infoMessage = '';
  testPrompt = '';
  testOutput = '';

  progressActive = false;
  progressPercent = -1;
  progressLabel = '';
  progressSize = '';

  installedModels: string[] = [];
  selectedInstalledModel = '';
  selectedPresetId = 'smollm2-135m-q8';
  customModelUrl = '';
  customModelName = '';
  customModelSha256 = '';

  readonly modelPresets: LlmModelPreset[] = [
    {
      id: 'smollm2-135m-q8',
      label: 'SmolLM2 135M Instruct Q8_0 (Standard)',
      modelName: 'SmolLM2-135M-Instruct-Q8_0.gguf',
      modelUrl: 'https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/main/SmolLM2-135M-Instruct-Q8_0.gguf',
      modelSha256: '5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83',
      sizeHint: '~139 MB',
    },
    {
      id: 'qwen2-0-5b-q4-km',
      label: 'Qwen2.5 0.5B Instruct Q4_K_M',
      modelName: 'Qwen2.5-0.5B-Instruct-Q4_K_M.gguf',
      modelUrl: 'https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf',
      sizeHint: '~400 MB',
    },
    {
      id: 'llama3-2-1b-q4-km',
      label: 'Llama 3.2 1B Instruct Q4_K_M',
      modelName: 'Llama-3.2-1B-Instruct-Q4_K_M.gguf',
      modelUrl: 'https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf',
      sizeHint: '~800 MB',
    },
    {
      id: 'smollm2-1-7b-q4-km',
      label: 'SmolLM2 1.7B Instruct Q4_K_M',
      modelName: 'SmolLM2-1.7B-Instruct-Q4_K_M.gguf',
      modelUrl: 'https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF/resolve/main/SmolLM2-1.7B-Instruct-Q4_K_M.gguf',
      sizeHint: '~1.1 GB',
    },
  ];

  private removeProgressListener?: () => Promise<void>;

  get isAndroidNative(): boolean {
    return this.runtime.isNative && Capacitor.getPlatform() === 'android';
  }

  get selectedPreset(): LlmModelPreset | undefined {
    return this.modelPresets.find((preset) => preset.id === this.selectedPresetId);
  }

  get isCustomPreset(): boolean {
    return this.selectedPresetId === 'custom';
  }

  get canInstallSelectedModel(): boolean {
    if (!this.isCustomPreset) return !!this.selectedPreset;
    return !!this.customModelUrl.trim() && !!this.customModelName.trim();
  }

  ngOnInit(): void {
    if (!this.isAndroidNative) return;
    this.runtime.onLlmInstallProgress((event) => this.onProgress(event)).then(
      (remove) => { this.removeProgressListener = remove; }
    ).catch(() => undefined);
    this.refreshStatus();
  }

  ngOnDestroy(): void {
    if (this.removeProgressListener) {
      this.removeProgressListener().catch(() => undefined);
    }
  }

  async refreshStatus(): Promise<void> {
    await this.run(async () => {
      this.status = await this.runtime.getLlmSetupStatus();
      const installed = await this.runtime.listInstalledModels();
      this.installedModels = installed.models ?? [];
      this.selectedInstalledModel = installed.activeModel || this.installedModels[0] || '';
    });
  }

  async installServer(): Promise<void> {
    this.progressActive = true;
    this.progressLabel = 'LLM Server wird installiert...';
    this.progressPercent = -1;
    await this.run(async () => {
      await this.runtime.installLlamaServer();
      this.toast.success('LLM Server installiert.');
      await this.refreshStatusQuiet();
    });
    this.progressActive = false;
  }

  async installModel(): Promise<void> {
    this.progressActive = true;
    this.progressLabel = 'Modell wird heruntergeladen...';
    this.progressPercent = -1;
    await this.run(async () => {
      if (this.isCustomPreset) {
        await this.runtime.installModel({
          modelName: this.customModelName.trim(),
          modelUrl: this.customModelUrl.trim(),
          modelSha256: this.customModelSha256.trim() || undefined,
        });
      } else {
        const preset = this.selectedPreset;
        if (!preset) throw new Error('Kein Modell-Preset ausgewaehlt.');
        await this.runtime.installModel({
          modelName: preset.modelName,
          modelUrl: preset.modelUrl,
          modelSha256: preset.modelSha256,
        });
      }
      this.toast.success('Modell heruntergeladen.');
      await this.refreshStatusQuiet();
    });
    this.progressActive = false;
  }

  async activateSelectedModel(): Promise<void> {
    if (!this.selectedInstalledModel) return;
    await this.run(async () => {
      await this.runtime.setActiveModel(this.selectedInstalledModel);
      this.toast.success('Aktives Modell gesetzt: ' + this.selectedInstalledModel);
      await this.refreshStatusQuiet();
    });
  }

  async startServer(): Promise<void> {
    this.progressActive = true;
    this.progressLabel = 'Server wird gestartet...';
    this.progressPercent = -1;
    await this.run(async () => {
      const result = await this.runtime.startLlmServer();
      this.toast.success('LLM Server laeuft auf Port ' + result.port);
      this.infoMessage = 'Server laeuft. API: http://127.0.0.1:' + result.port + '/v1';
      await this.refreshStatusQuiet();
    });
    this.progressActive = false;
  }

  async stopServer(): Promise<void> {
    await this.run(async () => {
      await this.runtime.stopLlmServer();
      this.toast.info('Server gestoppt.');
      this.infoMessage = '';
      await this.refreshStatusQuiet();
    });
  }

  async checkHealth(): Promise<void> {
    await this.run(async () => {
      const result = await this.runtime.getLlmServerHealth();
      if (result.ok) {
        this.infoMessage = 'Health: OK\n' + (result.response || '');
        this.toast.success('Server ist gesund.');
      } else {
        this.errorMessage = 'Health Check fehlgeschlagen: ' + (result.error || 'unbekannt');
      }
    });
  }

  async testGenerate(): Promise<void> {
    this.testOutput = '';
    await this.run(async () => {
      const response = await fetch('http://127.0.0.1:' + this.status.serverPort + '/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: this.status.modelName || 'default',
          messages: [{ role: 'user', content: this.testPrompt }],
          max_tokens: 256,
        }),
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      this.testOutput = data?.choices?.[0]?.message?.content || '(keine Antwort)';
    });
  }

  private async refreshStatusQuiet(): Promise<void> {
    try {
      this.status = await this.runtime.getLlmSetupStatus();
      const installed = await this.runtime.listInstalledModels();
      this.installedModels = installed.models ?? [];
      this.selectedInstalledModel = installed.activeModel || this.installedModels[0] || '';
    } catch {}
  }

  private onProgress(event: LlmInstallProgressEvent): void {
    this.progressActive = true;
    const progress = Number(event?.progress);
    this.progressPercent = Number.isFinite(progress) && progress >= 0
      ? Math.max(0, Math.min(100, Math.round(progress * 100)))
      : -1;
    this.progressLabel = String(event?.message || event?.stage || 'Installiere...');
    if (event.downloadedBytes > 0) {
      const mb = (event.downloadedBytes / (1024 * 1024)).toFixed(1);
      const totalMb = event.totalBytes > 0 ? (event.totalBytes / (1024 * 1024)).toFixed(1) : '?';
      this.progressSize = mb + ' / ' + totalMb + ' MB';
    } else {
      this.progressSize = '';
    }
  }

  private async run(work: () => Promise<void>): Promise<void> {
    this.busy = true;
    this.errorMessage = '';
    try {
      await work();
    } catch (error: any) {
      const message = error?.message || String(error);
      this.errorMessage = message;
      this.toast.error(message);
    } finally {
      this.busy = false;
    }
  }
}

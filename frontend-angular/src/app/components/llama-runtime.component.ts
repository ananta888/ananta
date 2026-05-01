import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Capacitor } from '@capacitor/core';

import { LlamaRuntimeService, LlmSetupStatus, LlmInstallProgressEvent } from '../services/llama-runtime.service';
import { ToastService } from '../services/toast.service';

@Component({
  standalone: true,
  selector: 'app-llama-runtime',
  imports: [FormsModule],
  template: `
    <section class="card runtime-page">
      <h2>LLM Runtime Setup</h2>
      <p class="muted">Lokales KI-Modell herunterladen, starten und nutzen — alles direkt auf dem Geraet.</p>

      @if (!isAndroidNative) {
        <div class="card card-light">Nur in der nativen Android-App verfuegbar.</div>
      } @else {

        <!-- Status Overview -->
        <div class="card card-light mt-sm status-grid">
          <div class="status-row">
            <span class="status-icon">{{ status.prootReady ? '✅' : '❌' }}</span>
            <span>1. Ubuntu Runtime</span>
            <span class="muted">{{ status.prootReady ? 'Bereit' : 'Nicht installiert — bitte zuerst unter Mobile Shell einrichten' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.serverInstalled ? '✅' : '❌' }}</span>
            <span>2. LLM Server (llama.cpp {{ status.llamaVersion }})</span>
            <span class="muted">{{ status.serverInstalled ? 'Installiert' : 'Nicht installiert' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.modelInstalled ? '✅' : '❌' }}</span>
            <span>3. KI-Modell ({{ status.modelName || 'SmolLM2-135M' }})</span>
            <span class="muted">{{ status.modelInstalled ? 'Heruntergeladen' : 'Nicht heruntergeladen (~139 MB)' }}</span>
          </div>
          <div class="status-row">
            <span class="status-icon">{{ status.serverRunning ? '🟢' : '⚪' }}</span>
            <span>4. Server-Status</span>
            <span class="muted">{{ status.serverRunning ? 'Laeuft auf Port ' + status.serverPort : 'Gestoppt' }}</span>
          </div>
        </div>

        <!-- Progress Bar -->
        @if (progressActive) {
          <div class="card card-light mt-sm">
            <div class="muted">{{ progressLabel }}</div>
            @if (progressPercent >= 0) {
              <progress [value]="progressPercent" max="100"></progress>
              <div class="muted progress-detail">{{ progressPercent }}%{{ progressSize ? ' — ' + progressSize : '' }}</div>
            }
          </div>
        }

        <!-- Action Buttons -->
        <div class="row gap-sm mt-md wrap">
          <button type="button" class="secondary" (click)="refreshStatus()" [disabled]="busy">
            Status aktualisieren
          </button>
          <button type="button" class="primary" (click)="installServer()" [disabled]="busy || !status.prootReady || status.serverInstalled">
            {{ status.serverInstalled ? 'Server installiert ✓' : 'LLM Server installieren' }}
          </button>
          <button type="button" class="primary" (click)="installModel()" [disabled]="busy || !status.serverInstalled || status.modelInstalled">
            {{ status.modelInstalled ? 'Modell vorhanden ✓' : 'Modell herunterladen (~139 MB)' }}
          </button>
        </div>

        <div class="row gap-sm mt-sm wrap">
          @if (!status.serverRunning) {
            <button type="button" class="primary" (click)="startServer()"
              [disabled]="busy || !status.serverInstalled || !status.modelInstalled || !status.prootReady">
              Server starten
            </button>
          } @else {
            <button type="button" class="secondary" (click)="stopServer()" [disabled]="busy">
              Server stoppen
            </button>
          }
          <button type="button" class="secondary" (click)="checkHealth()" [disabled]="busy || !status.serverRunning">
            Health Check
          </button>
        </div>

        <!-- Error / Info -->
        @if (errorMessage) {
          <pre class="card card-light error-box mt-sm">{{ errorMessage }}</pre>
        }
        @if (infoMessage) {
          <pre class="card card-light output-box mt-sm">{{ infoMessage }}</pre>
        }

        <!-- Quick Test -->
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
    .field input, .field textarea {
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

  private removeProgressListener?: () => Promise<void>;

  get isAndroidNative(): boolean {
    return this.runtime.isNative && Capacitor.getPlatform() === 'android';
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
      await this.runtime.installModel();
      this.toast.success('Modell heruntergeladen.');
      await this.refreshStatusQuiet();
    });
    this.progressActive = false;
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
    try { this.status = await this.runtime.getLlmSetupStatus(); } catch {}
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

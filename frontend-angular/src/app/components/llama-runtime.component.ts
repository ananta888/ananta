import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { LlamaRuntimeService } from '../services/llama-runtime.service';
import { ToastService } from '../services/toast.service';

@Component({
  standalone: true,
  selector: 'app-llama-runtime',
  imports: [FormsModule],
  template: `
    <section class="card runtime-page">
      <h2>Lokale LLM Runtime (llama.cpp)</h2>
      <p class="muted">Modell laden, Prompt senden und laufende Generierung abbrechen.</p>

      <div class="grid gap-sm mt-md">
        <label class="field">
          <span>Model-Pfad (.gguf)</span>
          <input [(ngModel)]="modelPath" placeholder="/data/user/0/com.ananta.mobile/files/models/model.gguf" />
        </label>
        <label class="field">
          <span>Threads</span>
          <input type="number" min="1" max="16" [(ngModel)]="threads" />
        </label>
        <label class="field">
          <span>Context Size</span>
          <input type="number" min="256" max="8192" [(ngModel)]="contextSize" />
        </label>
      </div>

      <div class="row gap-sm mt-md wrap">
        <button class="secondary" type="button" (click)="health()" [disabled]="busy">Runtime-Status</button>
        <button class="primary" type="button" (click)="loadModel()" [disabled]="busy || !modelPath.trim()">Modell laden</button>
        <button class="secondary" type="button" (click)="unloadModel()" [disabled]="busy">Modell entladen</button>
      </div>

      <label class="field mt-md">
        <span>Prompt</span>
        <textarea rows="8" [(ngModel)]="prompt"></textarea>
      </label>
      <div class="row gap-sm mt-md wrap">
        <button class="primary" type="button" (click)="generate()" [disabled]="busy || !prompt.trim()">Generieren</button>
        <button class="secondary" type="button" (click)="stopGeneration()" [disabled]="busy">Generierung abbrechen</button>
      </div>

      <div class="grid gap-sm mt-md">
        <div><strong>Native:</strong> {{ nativeAvailable ? 'ja' : 'nein' }}</div>
        <div><strong>Geladenes Modell:</strong> {{ loadedModel || '-' }}</div>
      </div>

      @if (errorMessage) {
        <pre class="card card-light error-box">{{ errorMessage }}</pre>
      }
      @if (output) {
        <pre class="card card-light output-box">{{ output }}</pre>
      }
    </section>
  `,
  styles: [`
    .runtime-page { max-width: 920px; margin: 0 auto; }
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
  `],
})
export class LlamaRuntimeComponent {
  private readonly runtime = inject(LlamaRuntimeService);
  private readonly toast = inject(ToastService);

  busy = false;
  nativeAvailable = false;
  modelPath = '';
  loadedModel = '';
  threads = 2;
  contextSize = 2048;
  prompt = '';
  output = '';
  errorMessage = '';

  async health(): Promise<void> {
    await this.run(async () => {
      const status = await this.runtime.health();
      this.nativeAvailable = !!status.nativeAvailable;
      this.toast.info(`Native Runtime: ${this.nativeAvailable ? 'verfuegbar' : 'nicht verfuegbar'}`);
    });
  }

  async loadModel(): Promise<void> {
    await this.run(async () => {
      const result = await this.runtime.loadModel(this.modelPath.trim(), this.threads, this.contextSize);
      this.loadedModel = result.modelPath;
      this.toast.success('Modell geladen.');
    });
  }

  async unloadModel(): Promise<void> {
    await this.run(async () => {
      await this.runtime.unloadModel();
      this.loadedModel = '';
      this.toast.info('Modell entladen.');
    });
  }

  async generate(): Promise<void> {
    await this.run(async () => {
      const result = await this.runtime.generate(this.prompt.trim(), 192);
      this.output = result.text || '';
      this.toast.success('Generierung abgeschlossen.');
    });
  }

  async stopGeneration(): Promise<void> {
    await this.run(async () => {
      await this.runtime.stopGeneration();
      this.toast.info('Generierung gestoppt.');
    });
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

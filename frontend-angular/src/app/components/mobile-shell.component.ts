import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Capacitor } from '@capacitor/core';

import { PythonRuntimeService, ShellCommandResult } from '../services/python-runtime.service';

@Component({
  standalone: true,
  selector: 'app-mobile-shell',
  imports: [FormsModule],
  template: `
    <section class="card shell-page">
      <h2>In-App Shell (Android)</h2>
      <p class="muted">Lokale Shell-Kommandos direkt in der nativen App ausfuehren.</p>

      @if (!isAndroidNative) {
        <div class="card card-light">Nur in der nativen Android-App verfuegbar.</div>
      } @else {
        <div class="grid gap-sm mt-sm">
          <label for="shell-command"><strong>Befehl</strong></label>
          <textarea
            id="shell-command"
            rows="4"
            [(ngModel)]="command"
            placeholder="z. B. pwd && ls -la"
            [disabled]="running"></textarea>
        </div>

        <div class="row gap-sm mt-sm wrap">
          <label>
            Timeout (s)
            <input type="number" min="1" max="600" [(ngModel)]="timeoutSeconds" [disabled]="running" />
          </label>
          <button type="button" class="primary" (click)="runCommand()" [disabled]="running">
            {{ running ? 'Laeuft...' : 'Ausfuehren' }}
          </button>
          <button type="button" class="secondary" (click)="setWorkerStartCommand()" [disabled]="running">Worker Start Vorlage</button>
          <button type="button" class="secondary" (click)="clearOutput()" [disabled]="running">Ausgabe leeren</button>
        </div>

        @if (lastMeta) {
          <div class="muted mt-sm">{{ lastMeta }}</div>
        }
        <pre class="card card-light shell-output mt-sm">{{ output || 'Noch keine Ausgabe.' }}</pre>
      }
    </section>
  `,
  styles: [`
    .shell-page {
      max-width: 980px;
      margin: 0 auto;
    }
    textarea {
      width: 100%;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }
    input[type="number"] {
      width: 96px;
      margin-left: 8px;
    }
    .shell-output {
      min-height: 220px;
      max-height: 58vh;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }
    .wrap {
      flex-wrap: wrap;
      align-items: center;
    }
  `],
})
export class MobileShellComponent {
  private python = inject(PythonRuntimeService);

  command = 'pwd && ls -la';
  timeoutSeconds = 20;
  running = false;
  output = '';
  lastMeta = '';

  get isAndroidNative(): boolean {
    return this.python.isNative && Capacitor.getPlatform() === 'android';
  }

  async runCommand(): Promise<void> {
    if (!this.isAndroidNative || this.running) return;
    this.running = true;
    this.lastMeta = '';
    try {
      const result = await this.python.runShellCommand(this.command, this.normalizedTimeout());
      this.applyResult(result);
    } catch (error: any) {
      this.output = (error?.message || String(error) || 'Unbekannter Fehler').trim();
      this.lastMeta = 'Fehler';
    } finally {
      this.running = false;
    }
  }

  setWorkerStartCommand(): void {
    this.command = [
      'cd /data/data/com.termux/files/home/ananta',
      'ROLE=worker AGENT_NAME=android-worker PORT=5001 HUB_URL=http://127.0.0.1:5000 AGENT_URL=http://127.0.0.1:5001',
      'python -m agent.ai_agent',
    ].join(' && ');
  }

  clearOutput(): void {
    this.output = '';
    this.lastMeta = '';
  }

  private normalizedTimeout(): number {
    const value = Number(this.timeoutSeconds);
    if (!Number.isFinite(value) || value < 1) return 20;
    return Math.min(600, Math.floor(value));
  }

  private applyResult(result: ShellCommandResult): void {
    this.output = result.output || '';
    const timeoutText = result.timedOut ? ' | Timeout' : '';
    this.lastMeta = `Exit-Code: ${result.exitCode}${timeoutText}`;
  }
}

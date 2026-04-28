import { Component, OnDestroy, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Capacitor } from '@capacitor/core';

import { PythonRuntimeService, ShellCommandResult } from '../services/python-runtime.service';

@Component({
  standalone: true,
  selector: 'app-mobile-shell',
  imports: [FormsModule],
  template: `
    <section class="card shell-page">
      <h2>In-App Terminal (Android)</h2>
      <p class="muted">Interaktive Shell-Session fuer lokale Befehle (inkl. proot-distro, falls verfuegbar).</p>

      @if (!isAndroidNative) {
        <div class="card card-light">Nur in der nativen Android-App verfuegbar.</div>
      } @else {
        <div class="card card-light grid gap-sm mt-sm">
          <div class="row gap-sm wrap">
            <button type="button" class="primary" (click)="startInteractiveShell()" [disabled]="shellBusy || shellRunning">Interaktive Shell starten</button>
            <button type="button" class="secondary" (click)="startProotSession()" [disabled]="shellBusy || shellRunning">proot-distro starten</button>
            <button type="button" class="secondary" (click)="closeInteractiveShell()" [disabled]="shellBusy || !shellSessionId">Session beenden</button>
            <button type="button" class="secondary" (click)="clearShellOutput()" [disabled]="shellBusy">Terminal leeren</button>
          </div>
          <div class="muted">Session: {{ shellSessionId || '-' }} | Status: {{ shellRunning ? 'aktiv' : 'inaktiv' }}</div>
          <div class="muted">{{ shellMeta || 'Noch keine Session gestartet.' }}</div>
          <pre class="card shell-output mt-sm">{{ shellOutput || 'Noch keine Ausgabe.' }}</pre>
          <label for="shell-input"><strong>Eingabe</strong></label>
          <textarea
            id="shell-input"
            rows="3"
            [(ngModel)]="shellInput"
            placeholder="Befehl eingeben, dann Senden oder Senden + Enter"
            [disabled]="shellBusy || !shellSessionId"></textarea>
          <div class="row gap-sm wrap">
            <button type="button" class="primary" (click)="sendInput(false)" [disabled]="shellBusy || !shellSessionId">Senden</button>
            <button type="button" class="secondary" (click)="sendInput(true)" [disabled]="shellBusy || !shellSessionId">Senden + Enter</button>
            <button type="button" class="secondary" (click)="sendCtrlC()" [disabled]="shellBusy || !shellSessionId">Ctrl+C</button>
            <button type="button" class="secondary" (click)="pullShellOutput()" [disabled]="shellBusy || !shellSessionId">Ausgabe aktualisieren</button>
          </div>
        </div>

        <details class="mt-sm">
          <summary>Einzelbefehl (ohne Session)</summary>
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
        </details>
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
export class MobileShellComponent implements OnDestroy {
  private python = inject(PythonRuntimeService);

  command = 'pwd && ls -la';
  timeoutSeconds = 20;
  running = false;
  output = '';
  lastMeta = '';
  shellSessionId = '';
  shellRunning = false;
  shellInput = '';
  shellBusy = false;
  shellOutput = '';
  shellMeta = '';
  private pollHandle?: ReturnType<typeof setInterval>;

  get isAndroidNative(): boolean {
    return this.python.isNative && Capacitor.getPlatform() === 'android';
  }

  ngOnDestroy(): void {
    this.stopPolling();
    if (this.shellSessionId) {
      this.python.closeShellSession(this.shellSessionId).catch(() => undefined);
    }
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

  async startInteractiveShell(): Promise<void> {
    if (!this.isAndroidNative || this.shellBusy || this.shellRunning) return;
    this.shellBusy = true;
    try {
      const started = await this.python.openShellSession({ shell: 'sh' });
      this.shellSessionId = started.sessionId;
      this.shellRunning = started.running;
      this.shellMeta = 'Interaktive Session gestartet.';
      this.startPolling();
      await this.pullShellOutput();
    } catch (error: any) {
      this.shellMeta = `Fehler: ${error?.message || String(error)}`;
    } finally {
      this.shellBusy = false;
    }
  }

  async startProotSession(): Promise<void> {
    if (!this.isAndroidNative || this.shellBusy || this.shellRunning) return;
    this.shellBusy = true;
    try {
      const started = await this.python.openShellSession({ shell: 'sh', initialCommand: this.prootBootstrapCommand() });
      this.shellSessionId = started.sessionId;
      this.shellRunning = started.running;
      this.shellMeta = 'proot-distro Startversuch ausgefuehrt.';
      this.startPolling();
      await this.pullShellOutput();
    } catch (error: any) {
      this.shellMeta = `Fehler: ${error?.message || String(error)}`;
    } finally {
      this.shellBusy = false;
    }
  }

  async closeInteractiveShell(): Promise<void> {
    if (!this.shellSessionId || this.shellBusy) return;
    this.shellBusy = true;
    try {
      await this.python.closeShellSession(this.shellSessionId);
      this.shellMeta = 'Session beendet.';
      this.shellSessionId = '';
      this.shellRunning = false;
      this.stopPolling();
    } catch (error: any) {
      this.shellMeta = `Fehler beim Beenden: ${error?.message || String(error)}`;
    } finally {
      this.shellBusy = false;
    }
  }

  async sendInput(appendNewline: boolean): Promise<void> {
    if (!this.shellSessionId || this.shellBusy) return;
    const payload = appendNewline ? `${this.shellInput}\n` : this.shellInput;
    if (!payload) return;
    this.shellBusy = true;
    try {
      await this.python.writeShellSession(this.shellSessionId, payload);
      if (appendNewline) this.shellInput = '';
      await this.pullShellOutput();
    } catch (error: any) {
      this.shellMeta = `Sendefehler: ${error?.message || String(error)}`;
    } finally {
      this.shellBusy = false;
    }
  }

  async sendCtrlC(): Promise<void> {
    if (!this.shellSessionId || this.shellBusy) return;
    this.shellBusy = true;
    try {
      await this.python.writeShellSession(this.shellSessionId, '\u0003');
      await this.pullShellOutput();
    } catch (error: any) {
      this.shellMeta = `Ctrl+C fehlgeschlagen: ${error?.message || String(error)}`;
    } finally {
      this.shellBusy = false;
    }
  }

  async pullShellOutput(): Promise<void> {
    if (!this.shellSessionId) return;
    try {
      while (true) {
        const chunk = await this.python.readShellSession(this.shellSessionId, 12000);
        if (chunk.output) this.shellOutput += chunk.output;
        this.shellRunning = chunk.running;
        if (!chunk.running) {
          this.shellMeta = `Session beendet (Exit-Code ${chunk.exitCode ?? -1}).`;
          this.stopPolling();
        }
        if (!chunk.hasMore) break;
      }
    } catch (error: any) {
      this.shellMeta = `Lesefehler: ${error?.message || String(error)}`;
      this.stopPolling();
      this.shellRunning = false;
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

  clearShellOutput(): void {
    this.shellOutput = '';
    this.shellMeta = '';
  }

  private startPolling(): void {
    this.stopPolling();
    this.pollHandle = setInterval(() => {
      this.pullShellOutput().catch(() => undefined);
    }, 1000);
  }

  private stopPolling(): void {
    if (!this.pollHandle) return;
    clearInterval(this.pollHandle);
    this.pollHandle = undefined;
  }

  private prootBootstrapCommand(): string {
    return [
      'if command -v proot-distro >/dev/null 2>&1; then',
      '  proot-distro login ubuntu;',
      'elif [ -x /data/data/com.termux/files/usr/bin/proot-distro ]; then',
      '  export PATH=/data/data/com.termux/files/usr/bin:$PATH;',
      '  /data/data/com.termux/files/usr/bin/proot-distro login ubuntu;',
      'else',
      '  echo "proot-distro nicht gefunden. Installiere/verwende es im gleichen Sandbox-Umfeld wie die App.";',
      'fi',
    ].join(' ');
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

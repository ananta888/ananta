import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Capacitor } from '@capacitor/core';

import { PythonRuntimeService, ShellCommandResult, ProotInstallProgressEvent, GuidedSetupStatus } from '../services/python-runtime.service';
import { MobileProotService } from '../services/mobile-proot.service';

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
          <div class="card card-light mt-sm">
            <strong>Gefuehrter Setup</strong>
            <div class="muted">1) Runtime installieren -> 2) Distro installieren -> 3) Worker + opencode Setup -> 4) Distro starten</div>
          </div>
          <div class="card card-light grid gap-sm mt-sm">
          <div class="row gap-sm wrap">
            <label>
              Distro
              <select [(ngModel)]="selectedDistro" (ngModelChange)="onDistroChange($event)" [disabled]="shellBusy || running">
                @for (distro of distroOptions; track distro) {
                  <option [value]="distro">{{ distro }}</option>
                }
              </select>
            </label>
            <button type="button" class="secondary" (click)="installRuntime()" [disabled]="running || prootBusy">1) Runtime installieren</button>
            <button type="button" class="secondary" (click)="installSelectedDistro()" [disabled]="running || prootBusy || !runtimeReady">2) Distro installieren</button>
            <button type="button" class="secondary" (click)="listInstalledDistros()" [disabled]="running || prootBusy">Installierte Distros</button>
            <button type="button" class="secondary" (click)="runCheckCommand()" [disabled]="running || prootBusy">Setup pruefen</button>
            <button type="button" class="secondary" (click)="setWorkerStartInDistroCommand()" [disabled]="running || !selectedDistroInstalled">Worker in Distro (Vorlage)</button>
          </div>
          <div class="muted">{{ prootStatus }}</div>
          @if (installProgressActive) {
            <div class="grid gap-sm">
              <div class="muted">{{ installProgressLabel }}</div>
              @if (installProgressPercent >= 0) {
                <progress [value]="installProgressPercent" max="100"></progress>
              }
            </div>
          }
          <div class="muted">
            Runtime: {{ runtimeReady ? 'ok' : (runtimeInstalled ? 'installiert, aber nicht startbar' : 'fehlt') }} | {{ selectedDistro }}: {{ selectedDistroInstalled ? 'installiert' : 'nicht installiert' }}
          </div>
          @if (runtimeProbeMessage && !runtimeReady) {
            <div class="muted">{{ runtimeProbeMessage }}</div>
          }
            <div class="muted">Installierte Distros: {{ installedDistros.length ? installedDistros.join(', ') : '-' }}</div>
            <hr />
            <div><strong>Schritt 3: Worker + opencode (fuer normale Anwender)</strong></div>
            <div class="muted">
              Workspace: {{ guidedStatus.workspaceInstalled ? 'ok' : 'fehlt' }} |
              Python: {{ guidedStatus.pythonReady ? 'ok' : 'fehlt' }} |
              pip: {{ guidedStatus.pipReady ? 'ok' : 'fehlt' }} |
              libgomp: {{ guidedStatus.libgompReady ? 'ok' : 'fehlt' }} |
              ananta: {{ guidedStatus.anantaCliReady ? 'ok' : 'fehlt' }} |
              ananta tui: {{ guidedStatus.anantaTuiReady ? 'ok' : 'fehlt' }} |
              ananta-worker: {{ guidedStatus.workerCommandReady ? 'ok' : 'fehlt' }} |
              Worker-Import: {{ guidedStatus.workerImportReady ? 'ok' : 'fehlt' }} |
              opencode: {{ guidedStatus.opencodeReady ? 'ok' : 'fehlt' }}
            </div>
            @if (guidedStatus.workerProbeMessage) {
              <div class="muted">{{ guidedStatus.workerProbeMessage }}</div>
            }
            <div class="row gap-sm wrap">
              <button type="button" class="secondary" (click)="installAnantaWorkspace()" [disabled]="running || prootBusy || !selectedDistroInstalled">3a) Workspace installieren</button>
              <button type="button" class="secondary" (click)="installWorkerDependencies()" [disabled]="running || prootBusy || !selectedDistroInstalled">3b) Worker-Dependencies</button>
              <button type="button" class="secondary" (click)="installOpencode()" [disabled]="running || prootBusy || !selectedDistroInstalled">3c) opencode installieren</button>
              <button type="button" class="secondary" (click)="refreshGuidedStatus()" [disabled]="running || prootBusy">Status neu pruefen</button>
            </div>
            <div class="row gap-sm wrap">
              <button type="button" class="primary" (click)="startInteractiveShell()" [disabled]="shellBusy || shellRunning">Interaktive Shell starten</button>
              <button type="button" class="secondary" (click)="startProotSession()" [disabled]="shellBusy || shellRunning">Distro-Session starten</button>
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
export class MobileShellComponent implements OnDestroy, OnInit {
  private python = inject(PythonRuntimeService);
  private proot = inject(MobileProotService);

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
  readonly distroOptions = this.proot.distroOptions;
  selectedDistro = this.proot.getSelectedDistro();
  prootBusy = false;
  prootStatus = '';
  installedDistros: string[] = [];
  runtimeInstalled = false;
  runtimeReady = false;
  runtimeProbeMessage = '';
  guidedStatus: GuidedSetupStatus = {
    runtimeInstalled: false,
    runtimeReady: false,
    ubuntuInstalled: false,
    pythonReady: false,
    pipReady: false,
    libgompReady: false,
    opencodeReady: false,
    anantaCliReady: false,
    anantaTuiReady: false,
    workerCommandReady: false,
    workspaceInstalled: false,
    workerImportReady: false,
  };
  installProgressActive = false;
  installProgressPercent = -1;
  installProgressLabel = '';
  private removeProotProgressListener?: () => Promise<void>;
  private pollHandle?: ReturnType<typeof setInterval>;

  get isAndroidNative(): boolean {
    return this.python.isNative && Capacitor.getPlatform() === 'android';
  }

  get selectedDistroInstalled(): boolean {
    return this.installedDistros.includes(this.selectedDistro);
  }

  ngOnInit(): void {
    if (!this.isAndroidNative) return;
    this.python.onProotInstallProgress((event) => this.onProotProgress(event)).then((remove) => {
      this.removeProotProgressListener = remove;
    }).catch(() => undefined);
    this.refreshProotRuntimeStatus().catch(() => undefined);
    this.refreshGuidedStatus().catch(() => undefined);
  }

  ngOnDestroy(): void {
    this.stopPolling();
    if (this.removeProotProgressListener) {
      this.removeProotProgressListener().catch(() => undefined);
    }
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
      this.proot.setSelectedDistro(this.selectedDistro);
      const started = await this.python.openShellSession({ shell: 'sh', initialCommand: this.proot.buildLoginCommand(this.selectedDistro) });
      this.shellSessionId = started.sessionId;
      this.shellRunning = started.running;
      this.shellMeta = `Distro-Session gestartet (${this.selectedDistro}).`;
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
    this.command = this.proot.buildWorkerStartCommand();
  }

  setWorkerStartInDistroCommand(): void {
    this.proot.setSelectedDistro(this.selectedDistro);
    this.command = this.proot.buildWorkerStartInDistroCommand(this.selectedDistro);
  }

  onDistroChange(next: string): void {
    this.selectedDistro = String(next || 'ubuntu').trim().toLowerCase();
    this.proot.setSelectedDistro(this.selectedDistro);
    this.refreshProotRuntimeStatus().catch(() => undefined);
  }

  runCheckCommand(): void {
    this.prootStatus = 'Pruefe Setup...';
    this.refreshProotRuntimeStatus().then(() => {
      this.output = [
        `Runtime: ${this.runtimeReady ? 'OK' : (this.runtimeInstalled ? 'installiert, aber nicht startbar' : 'fehlt')}`,
        ...(this.runtimeProbeMessage && !this.runtimeReady ? [`Runtime-Hinweis: ${this.runtimeProbeMessage}`] : []),
        `Distro ${this.selectedDistro}: ${this.selectedDistroInstalled ? 'installiert' : 'nicht installiert'}`,
        `Alle Distros: ${this.installedDistros.length ? this.installedDistros.join(', ') : '-'}`,
      ].join('\n');
      this.lastMeta = 'Setup-Pruefung';
      this.prootStatus = this.runtimeReady ? 'Setup geprueft.' : (
        this.runtimeInstalled
          ? 'Runtime vorhanden, aber nicht startbar.'
          : 'Runtime fehlt. Bitte Schritt 1 ausfuehren.'
      );
    }).catch((error: any) => {
      this.output = error?.message || String(error);
      this.lastMeta = 'Fehler';
      this.prootStatus = 'Setup-Pruefung fehlgeschlagen.';
    });
  }

  listInstalledDistros(): void {
    this.prootStatus = 'Lese installierte Distros...';
    this.refreshProotRuntimeStatus().then(() => {
      this.output = this.installedDistros.length
        ? this.installedDistros.map((name) => `- ${name}`).join('\n')
        : 'Keine Distros installiert.';
      this.lastMeta = 'Installierte Distros';
    }).catch((error: any) => {
      this.output = error?.message || String(error);
      this.lastMeta = 'Fehler';
    });
  }

  installSelectedDistro(): void {
    if (this.prootBusy) return;
    this.prootBusy = true;
    this.proot.setSelectedDistro(this.selectedDistro);
    this.prootStatus = `Installiere Distro ${this.selectedDistro}...`;
    this.python.installProotDistro(this.selectedDistro).then(
      (result) => {
        this.prootStatus = `Distro installiert: ${result.distro}`;
        this.refreshProotRuntimeStatus().catch(() => undefined);
        this.output = `Distro installiert: ${result.distro}\nRootfs: ${result.rootfsPath}`;
        this.lastMeta = 'Distro installiert';
      },
      (error: any) => {
        this.prootStatus = `Distro-Install fehlgeschlagen: ${error?.message || String(error)}`;
      }
    ).finally(() => {
      this.prootBusy = false;
      this.installProgressActive = false;
    });
  }

  installRuntime(): void {
    if (this.prootBusy) return;
    this.prootBusy = true;
    this.prootStatus = 'Installiere proot runtime...';
    this.python.installProotRuntime().then(
      () => this.python.getProotRuntimeStatus()
    ).then(
      (status) => {
        this.prootStatus = status.prootExecutable
          ? `Runtime installiert: ${status.prootPath}`
          : `Runtime installiert, aber nicht ausfuehrbar.${status.prootProbeMessage ? ` (${status.prootProbeMessage})` : ''}`;
        this.refreshProotRuntimeStatus().catch(() => undefined);
      },
      (error: any) => {
        this.prootStatus = `Runtime-Install fehlgeschlagen: ${error?.message || String(error)}`;
      }
    ).finally(() => {
      this.prootBusy = false;
      this.installProgressActive = false;
      this.refreshGuidedStatus().catch(() => undefined);
    });
  }

  installAnantaWorkspace(): void {
    if (this.prootBusy) return;
    this.prootBusy = true;
    this.prootStatus = 'Installiere Ananta-Workspace...';
    this.python.installAnantaWorkspace().then(
      (result) => {
        this.prootStatus = `Workspace installiert: ${result.workspacePath}`;
        this.output = `Workspace installiert.\nPfad: ${result.workspacePath}\nQuelle: ${result.repoUrl}`;
        this.lastMeta = 'Workspace installiert';
      },
      (error: any) => {
        this.prootStatus = `Workspace-Install fehlgeschlagen: ${error?.message || String(error)}`;
      }
    ).finally(() => {
      this.prootBusy = false;
      this.installProgressActive = false;
      this.refreshGuidedStatus().catch(() => undefined);
    });
  }

  installWorkerDependencies(): void {
    if (this.prootBusy) return;
    this.prootBusy = true;
    this.prootStatus = 'Installiere Worker-Dependencies...';
    this.python.installWorkerDependencies().then(
      (result) => {
        this.prootStatus = result.message || 'Worker-Dependencies installiert.';
        this.output = result.message || 'Worker-Dependencies installiert.';
        this.lastMeta = 'Worker-Dependencies';
      },
      (error: any) => {
        this.prootStatus = `Worker-Dependency-Install fehlgeschlagen: ${error?.message || String(error)}`;
      }
    ).finally(() => {
      this.prootBusy = false;
      this.installProgressActive = false;
      this.refreshGuidedStatus().catch(() => undefined);
    });
  }

  installOpencode(): void {
    if (this.prootBusy) return;
    this.prootBusy = true;
    this.prootStatus = 'Installiere opencode...';
    this.python.installOpencode().then(
      (result) => {
        this.prootStatus = `opencode installiert (${result.version})`;
        this.output = result.output || `opencode installiert (${result.version})`;
        this.lastMeta = 'opencode installiert';
      },
      (error: any) => {
        this.prootStatus = `opencode-Install fehlgeschlagen: ${error?.message || String(error)}`;
      }
    ).finally(() => {
      this.prootBusy = false;
      this.installProgressActive = false;
      this.refreshGuidedStatus().catch(() => undefined);
    });
  }

  async refreshGuidedStatus(): Promise<void> {
    try {
      const status = await this.python.getGuidedSetupStatus();
      this.guidedStatus = status;
    } catch {
      // keep current status when guided status query fails
    }
  }

  private async refreshProotRuntimeStatus(): Promise<void> {
    const status = await this.python.getProotRuntimeStatus();
    this.runtimeInstalled = status.prootExists === true;
    this.runtimeReady = status.prootExecutable === true;
    this.runtimeProbeMessage = String(status.prootProbeMessage || '').trim();
    this.installedDistros = (status.distros || [])
      .map((item) => String(item?.name || '').trim())
      .filter(Boolean)
      .sort();
    if (!this.prootStatus) {
      if (status.prootExecutable) {
        this.prootStatus = `Runtime bereit: ${status.prootPath}`;
      } else if (status.prootExists) {
        this.prootStatus = this.runtimeProbeMessage
          ? `Runtime installiert, aber nicht startbar: ${this.runtimeProbeMessage}`
          : 'Runtime installiert, aber nicht startbar.';
      } else {
        this.prootStatus = 'Runtime noch nicht installiert.';
      }
    }
  }

  private onProotProgress(event: ProotInstallProgressEvent): void {
    this.installProgressActive = true;
    const progress = Number(event?.progress);
    this.installProgressPercent = Number.isFinite(progress) && progress >= 0
      ? Math.max(0, Math.min(100, Math.round(progress * 100)))
      : -1;
    const baseLabel = String(event?.message || event?.stage || 'Installiere...');
    this.installProgressLabel = `${baseLabel}${this.progressDetailsSuffix(event)}`;
    const operation = String(event?.operation || '').trim();
    const distro = String(event?.distro || '').trim();
    const prefix = operation === 'distro'
      ? `Distro${distro ? ` (${distro})` : ''}`
      : operation === 'runtime'
        ? 'Runtime'
        : 'Setup';
    this.prootStatus = `${prefix}: ${this.installProgressLabel}`;
    if (event?.stage === 'done' || event?.stage === 'error') {
      if (event.stage === 'error') this.installProgressPercent = -1;
      this.refreshProotRuntimeStatus().catch(() => undefined);
      this.refreshGuidedStatus().catch(() => undefined);
    }
  }

  private progressDetailsSuffix(event: ProotInstallProgressEvent): string {
    const downloaded = Number(event?.downloadedBytes);
    const total = Number(event?.totalBytes);
    if (Number.isFinite(total) && total > 0 && Number.isFinite(downloaded) && downloaded >= 0) {
      return ` (${this.formatBytes(downloaded)} / ${this.formatBytes(total)})`;
    }
    if (Number.isFinite(downloaded) && downloaded > 0) {
      return ` (${this.formatBytes(downloaded)} geladen)`;
    }
    return '';
  }

  private formatBytes(bytes: number): string {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = Math.max(0, Number(bytes) || 0);
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    const decimals = value >= 100 || unitIndex === 0 ? 0 : 1;
    return `${value.toFixed(decimals)} ${units[unitIndex]}`;
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

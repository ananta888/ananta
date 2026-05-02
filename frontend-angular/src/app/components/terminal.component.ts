import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  Input,
  NgZone,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, finalize } from 'rxjs';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { TerminalMode, TerminalService } from '../services/terminal.service';
import { HubSystemApiClient } from '../services/hub-system-api.client';
import { NotificationService } from '../services/notification.service';
import { PythonRuntimeService } from '../services/python-runtime.service';

const TERMINAL_LOW_LATENCY_STORAGE_KEY = 'ananta.terminal.low-latency';
const TERMINAL_OUTPUT_MIRROR_ENABLED = true;

type TerminalButtonKeySpec = {
  key: string;
  code: string;
  keyCode?: number;
  charCode?: number;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  metaKey?: boolean;
};

@Component({
  standalone: true,
  selector: 'app-terminal',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule],
  providers: [TerminalService],
  styles: [`
    .terminal-shell {
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: #05070d;
    }
    .terminal-toolbar {
      display: flex;
      gap: 8px;
      padding: 8px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      align-items: center;
      flex-wrap: wrap;
    }
    .terminal-host {
      min-height: 320px;
      padding: 6px;
    }
    .terminal-toolbar input {
      min-width: 0;
      flex: 1;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
    }
    .terminal-control-row {
      display: flex;
      gap: 6px;
      width: 100%;
      flex-wrap: wrap;
    }
    .terminal-control-row button {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
      padding: 4px 8px;
    }
    .status-pill {
      font-size: 12px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
    }
    .terminal-copy-panel {
      border-top: 1px solid var(--border);
      background: var(--card-bg);
      padding: 8px;
    }
    .terminal-copy-panel summary {
      cursor: pointer;
      user-select: none;
      font-size: 12px;
      color: var(--muted-text);
    }
    .terminal-copy-panel textarea {
      margin-top: 8px;
      width: 100%;
      min-height: 120px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
      font-size: 12px;
    }
    @media (max-width: 900px) {
      .terminal-host {
        min-height: 240px;
      }
      .terminal-toolbar {
        gap: 6px;
      }
      .terminal-toolbar input {
        width: 100%;
      }
    }
  `],
  template: `
    <div class="terminal-shell">
      <div class="terminal-toolbar">
        <span class="status-pill">Status: {{status}}</span>
        <button (click)="reconnect()">Neu verbinden</button>
        <button class="button-outline" (click)="restartVisibleTerminal()" [disabled]="restartBusy || workerRestartBusy">
          {{ forwardParam ? 'Terminal neu starten' : 'Terminal neu verbinden' }}
        </button>
        <button class="button-outline" (click)="restartWorker()" [disabled]="workerRestartBusy || restartBusy">
          Worker neu starten
        </button>
        <button class="button-outline" (click)="clear()">Leeren</button>
        <button class="button-outline" type="button" (click)="toggleLowLatencyMode()">
          {{ lowLatencyMode ? 'Low Latency an' : 'Low Latency aus' }}
        </button>
        <button class="button-outline" type="button" (click)="copyTerminalOutput()" [disabled]="!outputBuffer">
          Ausgabe kopieren
        </button>
        @if (mode === 'interactive') {
          <div class="terminal-control-row">
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u0003')">Ctrl+C</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u0004')">Ctrl+D</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001a')">Ctrl+Z</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u000c')">Ctrl+L</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001b')">Esc</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\t')">Tab</button>
            <button type="button" class="button-outline" (click)="sendEnterKey()">Enter</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u007f')">Backspace</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001b[A')">↑</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001b[B')">↓</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001b[D')">←</button>
            <button type="button" class="button-outline" (click)="sendSpecialInput('\u001b[C')">→</button>
          </div>
          <input
            [(ngModel)]="quickCommand"
            (keydown.enter)="sendQuickCommand()"
            placeholder="z.B. echo hello"
            aria-label="Terminal-Befehl"
          />
          <button (click)="sendQuickCommand()" [disabled]="!quickCommand.trim()">Senden</button>
        } @else {
          <span class="muted">Read-only Stream</span>
        }
      </div>
      <div #terminalHost class="terminal-host" aria-label="Terminal output" (click)="focusTerminal()"></div>
      @if (outputBuffer) {
        <details class="terminal-copy-panel">
          <summary>Kopierbare Ausgabe</summary>
          <textarea [value]="outputBuffer" readonly aria-label="Kopierbare Terminal-Ausgabe"></textarea>
        </details>
      }
    </div>
    @if (outputMirrorEnabled) {
      <pre data-testid="terminal-output-buffer" style="display:none;">{{outputBuffer}}</pre>
    }
  `,
})
export class TerminalComponent implements AfterViewInit, OnChanges, OnDestroy {
  private terminalService = inject(TerminalService);
  private systemApi = inject(HubSystemApiClient);
  private notifications = inject(NotificationService);
  private pythonRuntime = inject(PythonRuntimeService);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);

  @Input({ required: true }) baseUrl = '';
  @Input() token?: string;
  @Input() mode: TerminalMode = 'interactive';
  @Input() forwardParam?: string;
  @Input() embeddedShellMode = false;
  @Input() embeddedInitialCommand?: string;

  @ViewChild('terminalHost', { static: true }) terminalHost?: ElementRef<HTMLDivElement>;

  private terminal?: Terminal;
  private fitAddon = new FitAddon();
  private subs: Subscription[] = [];
  private resizeObserver?: ResizeObserver;
  private initialized = false;
  private lastConnectKey = '';
  private pendingOutput = '';
  private flushScheduled = false;
  private mirroredOutputBuffer = '';
  private mirrorFlushHandle?: number;
  private resizeDebounceHandle?: number;
  private lastSentCols = 0;
  private lastSentRows = 0;
  private embeddedSessionId = '';
  private embeddedPollHandle?: ReturnType<typeof setInterval>;

  status = 'idle';
  quickCommand = '';
  outputBuffer = '';
  restartBusy = false;
  workerRestartBusy = false;
  lowLatencyMode = this.readLowLatencyPreference();
  outputMirrorEnabled = TERMINAL_OUTPUT_MIRROR_ENABLED;

  ngAfterViewInit(): void {
    if (!this.terminalHost) return;

    this.terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
      fontSize: 13,
      theme: {
        background: '#05070d',
        foreground: '#dbe4ff',
        cursor: '#8db3ff'
      },
    });

    this.terminal.loadAddon(this.fitAddon);
    this.terminal.open(this.terminalHost.nativeElement);
    this.fitToContainer(true);
    this.focusTerminal();

    this.zone.runOutsideAngular(() => {
      this.terminal?.onData((data) => {
        if (this.mode !== 'interactive') return;
        if (this.embeddedShellMode) {
          this.echoLocalInput(data);
        }
        this.sendInputToBackend(data);
      });
    });

    if (!this.embeddedShellMode) {
      this.subs.push(
        this.terminalService.state$.subscribe((state) => {
          if (this.status === state) return;
          this.zone.run(() => {
            this.status = state;
            this.cdr.markForCheck();
          });
        })
      );

      this.zone.runOutsideAngular(() => {
        this.subs.push(
          this.terminalService.output$.subscribe((chunk) => {
            this.bufferOutput(chunk);
          })
        );
      });

      this.subs.push(
        this.terminalService.events$.subscribe((evt) => {
          if (evt.type === 'ready') {
            const modeLabel = evt.data?.read_only ? 'read-only' : 'interactive';
            const marker = `\r\n[connected: ${modeLabel}]\r\n`;
            this.zone.runOutsideAngular(() => {
              this.terminal?.writeln(marker);
            });
            this.appendMirroredOutput(marker);
            this.zone.run(() => {
              this.fitToContainer(true);
              this.focusTerminal();
              this.cdr.markForCheck();
            });
          }
          if (evt.type === 'error') {
            const detail = String(evt.data?.message || evt.data?.details || '').trim();
            const marker = detail ? `\r\n[connection error: ${detail}]\r\n` : '\r\n[connection error]\r\n';
            this.zone.runOutsideAngular(() => {
              this.terminal?.writeln(marker);
            });
            this.appendMirroredOutput(marker);
            this.zone.run(() => {
              this.cdr.markForCheck();
            });
          }
        })
      );
    }

    this.initialized = true;
    this.reconnect();
    if (typeof ResizeObserver !== 'undefined' && this.terminalHost?.nativeElement) {
      this.resizeObserver = new ResizeObserver(() => this.fitToContainer());
      this.resizeObserver.observe(this.terminalHost.nativeElement);
    }
    window.addEventListener('resize', this.onResize);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.initialized) return;
    if (changes['baseUrl'] || changes['token'] || changes['mode'] || changes['forwardParam'] || changes['embeddedShellMode'] || changes['embeddedInitialCommand']) {
      this.reconnect();
    }
  }

  reconnect(): void {
    if (this.embeddedShellMode) {
      this.reconnectEmbeddedShell();
      return;
    }
    if (!this.baseUrl) return;
    const connectKey = `${this.baseUrl}|${this.mode}|${this.token || ''}|${this.forwardParam || ''}`;
    if (connectKey === this.lastConnectKey && (this.status === 'connecting' || this.status === 'connected')) {
      return;
    }
    this.lastConnectKey = connectKey;
    this.terminal?.reset();
    this.resetMirroredOutput();
    this.quickCommand = '';
    this.status = 'connecting';
    void this.terminalService.connect({
      baseUrl: this.baseUrl,
      mode: this.mode,
      token: this.token,
      forwardParam: this.forwardParam,
    });
  }

  clear(): void {
    this.terminal?.clear();
    this.resetMirroredOutput();
  }

  restartVisibleTerminal(): void {
    if (this.embeddedShellMode) {
      this.reconnectEmbeddedShell();
      this.notifications.info('Embedded-Terminal neu verbunden.');
      return;
    }
    if (!this.baseUrl) return;
    if (!this.forwardParam) {
      this.reconnect();
      this.notifications.info('Terminal neu verbunden.');
      return;
    }
    this.restartBusy = true;
    this.systemApi.restartTerminalSession(this.baseUrl, this.forwardParam, this.token).pipe(
      finalize(() => {
        this.restartBusy = false;
        this.cdr.markForCheck();
      })
    ).subscribe({
      next: () => {
        this.reconnect();
        this.notifications.success('Terminal-Sitzung neu gestartet.');
      },
      error: (error) => {
        this.notifications.error(this.notifications.fromApiError(error, 'Terminal-Neustart fehlgeschlagen.'));
      },
    });
  }

  restartWorker(): void {
    if (this.embeddedShellMode) {
      this.workerRestartBusy = true;
      this.pythonRuntime.startWorker().then(() => {
        this.notifications.info('Embedded-Worker gestartet.');
      }).catch((error: any) => {
        this.notifications.error(error?.message || 'Embedded-Worker-Neustart nicht moeglich.');
      }).finally(() => {
        this.workerRestartBusy = false;
        this.cdr.markForCheck();
      });
      return;
    }
    if (!this.baseUrl) return;
    if (!confirm('Worker wirklich neu starten? Das trennt aktive Sitzungen kurzzeitig und ist nur als Eskalationsstufe gedacht.')) return;
    this.workerRestartBusy = true;
    this.systemApi.restartProcess(this.baseUrl, this.token).pipe(
      finalize(() => {
        this.workerRestartBusy = false;
        this.cdr.markForCheck();
      })
    ).subscribe({
      next: () => {
        this.terminalService.disconnect();
        this.notifications.info('Worker-Neustart angefordert. Nach dem Wiederanlauf erneut verbinden.');
      },
      error: (error) => {
        this.notifications.error(this.notifications.fromApiError(error, 'Worker-Neustart nicht moeglich.'));
      },
    });
  }

  focusTerminal(): void {
    this.terminal?.focus();
  }

  toggleLowLatencyMode(): void {
    this.lowLatencyMode = !this.lowLatencyMode;
    try {
      localStorage.setItem(TERMINAL_LOW_LATENCY_STORAGE_KEY, this.lowLatencyMode ? '1' : '0');
    } catch {}
    this.fitToContainer(true);
    this.notifications.info(this.lowLatencyMode ? 'Low-Latency-Terminalmodus aktiviert.' : 'Low-Latency-Terminalmodus deaktiviert.');
  }

  async copyTerminalOutput(): Promise<void> {
    const text = this.outputBuffer.trim();
    if (!text) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        this.notifications.success('Terminal-Ausgabe kopiert.');
        return;
      }
      this.notifications.info('Kopieren ueber "Kopierbare Ausgabe" unten.');
    } catch {
      this.notifications.info('Kopieren ueber "Kopierbare Ausgabe" unten.');
    }
  }

  sendSpecialInput(sequence: string): void {
    if (this.mode !== 'interactive' || !sequence) return;
    if (this.embeddedShellMode && sequence === '\u0003') {
      this.interruptEmbeddedShell();
      return;
    }
    this.focusTerminal();
    this.sendInputToBackend(sequence);
  }

  sendEnterKey(): void {
    if (this.mode !== 'interactive') return;
    this.focusTerminal();
    if (this.dispatchTerminalKey({
      key: 'Enter',
      code: 'Enter',
      keyCode: 13,
      charCode: 13,
    })) {
      return;
    }
    this.sendInputToBackend('\r');
  }

  sendQuickCommand(): void {
    if (this.mode !== 'interactive') return;
    const command = this.quickCommand.trim();
    if (!command) return;
    this.sendInputToBackend(`${command}\n`);
    this.quickCommand = '';
  }

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    this.resizeObserver?.disconnect();
    if (this.mirrorFlushHandle !== undefined) {
      window.clearTimeout(this.mirrorFlushHandle);
      this.mirrorFlushHandle = undefined;
    }
    if (this.resizeDebounceHandle !== undefined) {
      window.clearTimeout(this.resizeDebounceHandle);
      this.resizeDebounceHandle = undefined;
    }
    this.terminalService.disconnect();
    this.stopEmbeddedPolling();
    if (this.embeddedSessionId) {
      this.pythonRuntime.closeShellSession(this.embeddedSessionId).catch(() => undefined);
      this.embeddedSessionId = '';
    }
    this.subs.forEach((sub) => sub.unsubscribe());
    this.terminal?.dispose();
  }

  private onResize = () => {
    this.fitToContainer();
  };

  private fitToContainer(immediate = false): void {
    if (!this.terminal || !this.terminalHost?.nativeElement) return;
    if (this.resizeDebounceHandle !== undefined) {
      window.clearTimeout(this.resizeDebounceHandle);
      this.resizeDebounceHandle = undefined;
    }
    if (immediate) {
      this.applyFitToContainer();
      return;
    }
    this.resizeDebounceHandle = window.setTimeout(() => {
      this.resizeDebounceHandle = undefined;
      this.applyFitToContainer();
    }, this.lowLatencyMode ? 35 : 90);
  }

  private applyFitToContainer(): void {
    if (!this.terminal || !this.terminalHost?.nativeElement) return;
    this.fitAddon.fit();
    const cols = Math.max(1, this.terminal.cols);
    const rows = Math.max(1, this.terminal.rows);
    if (cols === this.lastSentCols && rows === this.lastSentRows) return;
    this.lastSentCols = cols;
    this.lastSentRows = rows;
    if (this.embeddedShellMode) return;
    this.terminalService.sendResize(cols, rows);
  }

  private bufferOutput(chunk: string): void {
    if (!chunk) return;
    this.pendingOutput += chunk;
    if (this.flushScheduled) return;
    this.flushScheduled = true;
    const schedule = this.lowLatencyMode
      ? ((cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 0))
      : (typeof requestAnimationFrame === 'function'
        ? requestAnimationFrame
        : ((cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16)));
    schedule(() => {
      const buffered = this.pendingOutput;
      this.pendingOutput = '';
      this.flushScheduled = false;
      this.zone.runOutsideAngular(() => {
        this.terminal?.write(buffered);
      });
      this.appendMirroredOutput(buffered);
    });
  }

  private appendMirroredOutput(chunk: string): void {
    if (!this.outputMirrorEnabled) return;
    if (!chunk) return;
    this.mirroredOutputBuffer = (this.mirroredOutputBuffer + chunk).slice(-12000);
    if (this.mirrorFlushHandle !== undefined) return;
    this.mirrorFlushHandle = window.setTimeout(() => {
      this.mirrorFlushHandle = undefined;
      this.zone.run(() => {
        this.outputBuffer = this.mirroredOutputBuffer;
        this.cdr.markForCheck();
      });
    }, this.lowLatencyMode ? 60 : 120);
  }

  private resetMirroredOutput(): void {
    this.mirroredOutputBuffer = '';
    this.outputBuffer = '';
    this.lastSentCols = 0;
    this.lastSentRows = 0;
    if (this.mirrorFlushHandle !== undefined) {
      window.clearTimeout(this.mirrorFlushHandle);
      this.mirrorFlushHandle = undefined;
    }
  }

  private readLowLatencyPreference(): boolean {
    try {
      return localStorage.getItem(TERMINAL_LOW_LATENCY_STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  }

  private dispatchTerminalKey(spec: TerminalButtonKeySpec): boolean {
    const textarea = this.terminalHost?.nativeElement.querySelector('textarea');
    if (!(textarea instanceof HTMLTextAreaElement)) return false;
    textarea.focus();
    const eventInit: KeyboardEventInit = {
      key: spec.key,
      code: spec.code,
      ctrlKey: spec.ctrlKey ?? false,
      altKey: spec.altKey ?? false,
      shiftKey: spec.shiftKey ?? false,
      metaKey: spec.metaKey ?? false,
      bubbles: true,
      cancelable: true,
    };
    const keydown = new KeyboardEvent('keydown', eventInit);
    Object.defineProperty(keydown, 'keyCode', { configurable: true, get: () => spec.keyCode ?? 0 });
    Object.defineProperty(keydown, 'which', { configurable: true, get: () => spec.keyCode ?? 0 });
    textarea.dispatchEvent(keydown);
    const keypress = new KeyboardEvent('keypress', eventInit);
    Object.defineProperty(keypress, 'keyCode', { configurable: true, get: () => spec.charCode ?? spec.keyCode ?? 0 });
    Object.defineProperty(keypress, 'charCode', { configurable: true, get: () => spec.charCode ?? spec.keyCode ?? 0 });
    Object.defineProperty(keypress, 'which', { configurable: true, get: () => spec.charCode ?? spec.keyCode ?? 0 });
    textarea.dispatchEvent(keypress);
    const keyup = new KeyboardEvent('keyup', eventInit);
    Object.defineProperty(keyup, 'keyCode', { configurable: true, get: () => spec.keyCode ?? 0 });
    Object.defineProperty(keyup, 'which', { configurable: true, get: () => spec.keyCode ?? 0 });
    textarea.dispatchEvent(keyup);
    return true;
  }

  private sendInputToBackend(input: string): void {
    if (!input) return;
    if (this.embeddedShellMode) {
      if (input === '\u0003') {
        this.interruptEmbeddedShell();
        return;
      }
      this.sendEmbeddedInput(input);
      return;
    }
    this.terminalService.sendInput(input);
  }

  private interruptEmbeddedShell(): void {
    const sessionId = this.embeddedSessionId;
    if (!sessionId) return;
    this.pythonRuntime.interruptShellSession(sessionId).then(() => {
      return new Promise<void>((resolve) => setTimeout(resolve, 10));
    }).then(() => this.pullEmbeddedOutput()).catch((error: any) => {
      const message = String(error?.message || 'embedded_shell_interrupt_failed').trim();
      this.status = 'error';
      this.writeTerminalMarker(`\r\n[input error: ${message}]\r\n`);
      this.cdr.markForCheck();
    });
  }

  private reconnectEmbeddedShell(): void {
    if (!this.pythonRuntime.isNative) {
      this.status = 'error';
      this.writeTerminalMarker('\r\n[embedded terminal unavailable]\r\n');
      return;
    }
    this.terminal?.reset();
    this.resetMirroredOutput();
    this.quickCommand = '';
    this.stopEmbeddedPolling();
    this.status = 'connecting';
    this.cdr.markForCheck();
    this.openEmbeddedShellSession();
  }

  private async openEmbeddedShellSession(): Promise<void> {
    try {
      if (this.embeddedSessionId) {
        await this.pythonRuntime.closeShellSession(this.embeddedSessionId).catch(() => undefined);
      }
      const initialCommand = String(this.embeddedInitialCommand || '').trim();
      const started = await this.pythonRuntime.openShellSession({
        shell: '/system/bin/sh',
        ...(initialCommand ? { initialCommand } : {}),
      });
      this.embeddedSessionId = started.sessionId;
      this.status = 'connected';
      this.writeTerminalMarker('\r\n[connected: embedded-interactive]\r\n');
      this.startEmbeddedPolling();
      this.fitToContainer(true);
      this.focusTerminal();
      this.cdr.markForCheck();
      await this.pullEmbeddedOutput();
    } catch (error: any) {
      this.status = 'error';
      const message = String(error?.message || 'embedded_shell_open_failed').trim();
      this.writeTerminalMarker(`\r\n[connection error: ${message}]\r\n`);
      this.cdr.markForCheck();
    }
  }

  private echoLocalInput(data: string): void {
    if (!this.terminal) return;
    let echo = '';
    for (const ch of data) {
      const code = ch.charCodeAt(0);
      if (ch === '\r' || ch === '\n') {
        echo += '\r\n';
      } else if (ch === '\x7f' || ch === '\b') {
        echo += '\b \b';
      } else if (code >= 32) {
        echo += ch;
      }
    }
    if (echo) this.terminal.write(echo);
  }

  private sendEmbeddedInput(input: string): void {
    const sessionId = this.embeddedSessionId;
    if (!sessionId) return;
    this.pythonRuntime.writeShellSession(sessionId, input).then(() => {
      // Small delay to let shell process input before reading
      return new Promise<void>((resolve) => setTimeout(resolve, 10));
    }).then(() => {
      return this.pullEmbeddedOutput();
    }).catch((error: any) => {
      const message = String(error?.message || 'embedded_shell_write_failed').trim();
      this.status = 'error';
      this.writeTerminalMarker(`\r\n[input error: ${message}]\r\n`);
      this.cdr.markForCheck();
    });
  }

  private startEmbeddedPolling(): void {
    this.stopEmbeddedPolling();
    const intervalMs = this.lowLatencyMode ? 80 : 200;
    this.embeddedPollHandle = setInterval(() => {
      this.pullEmbeddedOutput().catch(() => undefined);
    }, intervalMs);
  }

  private stopEmbeddedPolling(): void {
    if (!this.embeddedPollHandle) return;
    clearInterval(this.embeddedPollHandle);
    this.embeddedPollHandle = undefined;
  }

  private async pullEmbeddedOutput(): Promise<void> {
    const sessionId = this.embeddedSessionId;
    if (!sessionId) return;
    try {
      while (true) {
        const chunk = await this.pythonRuntime.readShellSession(sessionId, 12000);
        if (chunk.output) this.bufferOutput(chunk.output);
        if (!chunk.running) {
          this.status = 'disconnected';
          this.stopEmbeddedPolling();
          this.cdr.markForCheck();
        }
        if (!chunk.hasMore) break;
      }
    } catch (error: any) {
      const message = String(error?.message || 'embedded_shell_read_failed').trim();
      this.status = 'error';
      this.stopEmbeddedPolling();
      this.writeTerminalMarker(`\r\n[connection error: ${message}]\r\n`);
      this.cdr.markForCheck();
    }
  }

  private writeTerminalMarker(marker: string): void {
    if (!marker) return;
    this.zone.runOutsideAngular(() => {
      this.terminal?.writeln(marker);
    });
    this.appendMirroredOutput(marker);
  }
}

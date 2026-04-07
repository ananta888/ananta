import {
  AfterViewInit,
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

@Component({
  standalone: true,
  selector: 'app-terminal',
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
    .status-pill {
      font-size: 12px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
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
        @if (mode === 'interactive') {
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
    </div>
    <pre data-testid="terminal-output-buffer" style="display:none;">{{outputBuffer}}</pre>
  `,
})
export class TerminalComponent implements AfterViewInit, OnChanges, OnDestroy {
  private terminalService = inject(TerminalService);
  private systemApi = inject(HubSystemApiClient);
  private notifications = inject(NotificationService);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);

  @Input({ required: true }) baseUrl = '';
  @Input() token?: string;
  @Input() mode: TerminalMode = 'interactive';
  @Input() forwardParam?: string;

  @ViewChild('terminalHost', { static: true }) terminalHost?: ElementRef<HTMLDivElement>;

  private terminal?: Terminal;
  private fitAddon = new FitAddon();
  private subs: Subscription[] = [];
  private resizeObserver?: ResizeObserver;
  private initialized = false;
  private lastConnectKey = '';

  status = 'idle';
  quickCommand = '';
  outputBuffer = '';
  restartBusy = false;
  workerRestartBusy = false;

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
    this.fitToContainer();
    this.focusTerminal();

    this.terminal.onData((data) => {
      if (this.mode !== 'interactive') return;
      this.terminalService.sendInput(data);
    });

    this.subs.push(
      this.terminalService.state$.subscribe((state) => {
        this.zone.run(() => {
          this.status = state;
          this.cdr.detectChanges();
        });
      })
    );

    this.subs.push(
      this.terminalService.output$.subscribe((chunk) => {
        this.zone.run(() => {
          this.terminal?.write(chunk);
          this.outputBuffer = (this.outputBuffer + chunk).slice(-12000);
          this.cdr.detectChanges();
        });
      })
    );

    this.subs.push(
      this.terminalService.events$.subscribe((evt) => {
        if (evt.type === 'ready') {
          const modeLabel = evt.data?.read_only ? 'read-only' : 'interactive';
          const marker = `\r\n[connected: ${modeLabel}]\r\n`;
          this.zone.run(() => {
            this.terminal?.writeln(marker);
            this.outputBuffer = (this.outputBuffer + marker).slice(-12000);
            this.fitToContainer();
            this.focusTerminal();
            this.cdr.detectChanges();
          });
        }
        if (evt.type === 'error') {
          const marker = '\r\n[connection error]\r\n';
          this.zone.run(() => {
            this.terminal?.writeln(marker);
            this.outputBuffer = (this.outputBuffer + marker).slice(-12000);
            this.cdr.detectChanges();
          });
        }
      })
    );

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
    if (changes['baseUrl'] || changes['token'] || changes['mode'] || changes['forwardParam']) {
      this.reconnect();
    }
  }

  reconnect(): void {
    if (!this.baseUrl) return;
    const connectKey = `${this.baseUrl}|${this.mode}|${this.token || ''}|${this.forwardParam || ''}`;
    if (connectKey === this.lastConnectKey && (this.status === 'connecting' || this.status === 'connected')) {
      return;
    }
    this.lastConnectKey = connectKey;
    this.terminal?.reset();
    this.outputBuffer = '';
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
    this.outputBuffer = '';
  }

  restartVisibleTerminal(): void {
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
        this.cdr.detectChanges();
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
    if (!this.baseUrl) return;
    if (!confirm('Worker wirklich neu starten? Das trennt aktive Sitzungen kurzzeitig und ist nur als Eskalationsstufe gedacht.')) return;
    this.workerRestartBusy = true;
    this.systemApi.restartProcess(this.baseUrl, this.token).pipe(
      finalize(() => {
        this.workerRestartBusy = false;
        this.cdr.detectChanges();
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

  sendQuickCommand(): void {
    if (this.mode !== 'interactive') return;
    const command = this.quickCommand.trim();
    if (!command) return;
    this.terminalService.sendInput(`${command}\n`);
    this.quickCommand = '';
  }

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    this.resizeObserver?.disconnect();
    this.terminalService.disconnect();
    this.subs.forEach((sub) => sub.unsubscribe());
    this.terminal?.dispose();
  }

  private onResize = () => {
    this.fitToContainer();
  };

  private fitToContainer(): void {
    if (!this.terminal || !this.terminalHost?.nativeElement) return;
    this.fitAddon.fit();
    this.terminalService.sendResize(this.terminal.cols, this.terminal.rows);
  }
}

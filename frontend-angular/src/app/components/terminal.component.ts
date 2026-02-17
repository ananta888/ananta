import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { TerminalMode, TerminalService } from '../services/terminal.service';

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
        <button (click)="reconnect()">Reconnect</button>
        <button class="button-outline" (click)="clear()">Clear</button>
        @if (mode === 'interactive') {
          <input
            [(ngModel)]="quickCommand"
            (keydown.enter)="sendQuickCommand()"
            placeholder="echo hello"
            aria-label="Terminal command"
          />
          <button (click)="sendQuickCommand()" [disabled]="!quickCommand.trim()">Senden</button>
        } @else {
          <span class="muted">Read-only stream</span>
        }
      </div>
      <div #terminalHost class="terminal-host" aria-label="Terminal output"></div>
    </div>
    <pre data-testid="terminal-output-buffer" style="display:none;">{{outputBuffer}}</pre>
  `,
})
export class TerminalComponent implements AfterViewInit, OnChanges, OnDestroy {
  private terminalService = inject(TerminalService);

  @Input({ required: true }) baseUrl = '';
  @Input() token?: string;
  @Input() mode: TerminalMode = 'interactive';
  @Input() forwardParam?: string;

  @ViewChild('terminalHost', { static: true }) terminalHost?: ElementRef<HTMLDivElement>;

  private terminal?: Terminal;
  private fitAddon = new FitAddon();
  private subs: Subscription[] = [];
  private initialized = false;
  private lastConnectKey = '';

  status = 'idle';
  quickCommand = '';
  outputBuffer = '';

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
    this.fitAddon.fit();

    this.terminal.onData((data) => {
      if (this.mode !== 'interactive') return;
      this.terminalService.sendInput(data);
    });

    this.subs.push(
      this.terminalService.state$.subscribe((state) => {
        this.status = state;
      })
    );

    this.subs.push(
      this.terminalService.output$.subscribe((chunk) => {
        this.terminal?.write(chunk);
        this.outputBuffer = (this.outputBuffer + chunk).slice(-12000);
      })
    );

    this.subs.push(
      this.terminalService.events$.subscribe((evt) => {
        if (evt.type === 'ready') {
          const modeLabel = evt.data?.read_only ? 'read-only' : 'interactive';
          this.terminal?.writeln(`\r\n[connected: ${modeLabel}]\r\n`);
        }
        if (evt.type === 'error') {
          this.terminal?.writeln('\r\n[connection error]\r\n');
        }
      })
    );

    this.initialized = true;
    this.reconnect();
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

  sendQuickCommand(): void {
    if (this.mode !== 'interactive') return;
    const command = this.quickCommand.trim();
    if (!command) return;
    this.terminalService.sendInput(`${command}\n`);
    this.quickCommand = '';
  }

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    this.terminalService.disconnect();
    this.subs.forEach((sub) => sub.unsubscribe());
    this.terminal?.dispose();
  }

  private onResize = () => {
    this.fitAddon.fit();
  };
}

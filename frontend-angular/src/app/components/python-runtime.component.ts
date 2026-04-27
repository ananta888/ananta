import { Component, OnInit, inject } from '@angular/core';

import { ToastService } from '../services/toast.service';
import { PythonRuntimeService, PythonRuntimeStatus } from '../services/python-runtime.service';

@Component({
  standalone: true,
  selector: 'app-python-runtime',
  template: `
    <section class="card runtime-page">
      <h2>Python Runtime (optional)</h2>
      <p class="muted">Optionaler eingebetteter Hub/Worker-Betrieb in der nativen Android-App.</p>

      @if (!python.isNative) {
        <div class="card card-light">Nur in der nativen Android-App verfuegbar.</div>
      } @else {
        <div class="grid gap-sm mt-md">
          <div><strong>Python verfuegbar:</strong> {{ status.pythonAvailable ? 'ja' : 'nein' }}</div>
          <div><strong>Hub:</strong> {{ status.hubRunning ? 'aktiv' : 'aus' }}</div>
          <div><strong>Worker:</strong> {{ status.workerRunning ? 'aktiv' : 'aus' }}</div>
          <div><strong>Letzter Fehler:</strong> {{ status.lastError || '-' }}</div>
        </div>

        <div class="row gap-sm mt-md wrap">
          <button class="secondary" type="button" (click)="refresh()" [disabled]="busy">Status neu laden</button>
          <button class="primary" type="button" (click)="startHub()" [disabled]="busy || !status.pythonAvailable || status.hubRunning">Hub starten</button>
          <button class="secondary" type="button" (click)="stopHub()" [disabled]="busy || !status.hubRunning">Hub stoppen</button>
          <button class="primary" type="button" (click)="startWorker()" [disabled]="busy || !status.pythonAvailable || status.workerRunning">Worker starten</button>
          <button class="secondary" type="button" (click)="stopWorker()" [disabled]="busy || !status.workerRunning">Worker stoppen</button>
          <button class="secondary" type="button" (click)="healthCheck()" [disabled]="busy || !status.pythonAvailable">Health Check</button>
        </div>

        @if (healthMessage) {
          <pre class="card card-light mt-md">{{ healthMessage }}</pre>
        }
      }
    </section>
  `,
  styles: [`
    .runtime-page {
      max-width: 920px;
      margin: 0 auto;
    }
    .wrap {
      flex-wrap: wrap;
    }
  `],
})
export class PythonRuntimeComponent implements OnInit {
  python = inject(PythonRuntimeService);
  private toast = inject(ToastService);

  busy = false;
  healthMessage = '';
  status: PythonRuntimeStatus = {
    pythonAvailable: false,
    hubRunning: false,
    workerRunning: false,
  };

  async ngOnInit(): Promise<void> {
    await this.refresh();
  }

  async refresh(): Promise<void> {
    await this.run(async () => {
      this.status = await this.python.getRuntimeStatus();
    }, false);
  }

  async startHub(): Promise<void> {
    await this.run(async () => {
      await this.python.startHub();
      await this.refresh();
      this.toast.success('Hub gestartet.');
    });
  }

  async stopHub(): Promise<void> {
    await this.run(async () => {
      await this.python.stopHub();
      await this.refresh();
      this.toast.info('Hub gestoppt.');
    });
  }

  async startWorker(): Promise<void> {
    await this.run(async () => {
      await this.python.startWorker();
      await this.refresh();
      this.toast.success('Worker gestartet.');
    });
  }

  async stopWorker(): Promise<void> {
    await this.run(async () => {
      await this.python.stopWorker();
      await this.refresh();
      this.toast.info('Worker gestoppt.');
    });
  }

  async healthCheck(): Promise<void> {
    await this.run(async () => {
      const result = await this.python.runHealthCheck();
      this.healthMessage = result.message || '';
      this.toast.success('Health Check erfolgreich.');
    });
  }

  private async run(work: () => Promise<void>, showErrorToast = true): Promise<void> {
    this.busy = true;
    try {
      await work();
    } catch (error: any) {
      const message = error?.message || String(error);
      if (showErrorToast) this.toast.error(message);
    } finally {
      this.busy = false;
    }
  }
}

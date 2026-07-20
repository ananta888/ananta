import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { Subject, of } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  SpeechReconciliationApiService,
  SpeechReconciliationJobView,
  SpeechResourceVectorView,
} from '../../services/speech-reconciliation-api.service';
import { SpeechReconciliationPanelComponent } from './speech-reconciliation-panel.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

const vector = (value: number): SpeechResourceVectorView => ({
  wall_time_ms: value,
  cpu_time_ms: value,
  gpu_time_ms: value,
  memory_byte_ms: value,
  disk_bytes: value,
  checkpoint_bytes: value,
  energy_millijoules: value,
});

const projection = (overrides: Partial<SpeechReconciliationJobView> = {}): SpeechReconciliationJobView => ({
  job_id: 'speech-reconciliation-job-1',
  state: 'running',
  stage: 'alignment',
  reason_code: 'speech_reconciliation_running',
  source_duration_ms: 90_000,
  max_compute_factor: 10,
  ledger_sequence: 3,
  key_epoch: 2,
  checkpoint_count: 4,
  conflict_counts: { resolved: 8, unresolved: 2, rejected: 1, quarantined: 3 },
  budget: {
    allocated: vector(10), reserved: vector(2), consumed: vector(3), remaining: vector(5),
  },
  active_attempt_id: 'speech-reconciliation-attempt-1',
  version: 7,
  created_at_ms: 1_700_000_000_000,
  updated_at_ms: 1_700_000_001_000,
  finished_at_ms: null,
  ...overrides,
});

describe('SpeechReconciliationPanelComponent', () => {
  const api = {
    list: vi.fn(),
    get: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    cancel: vi.fn(),
    reduce: vi.fn(),
  };
  let fixture: ComponentFixture<SpeechReconciliationPanelComponent>;

  beforeEach(async () => {
    vi.clearAllMocks();
    api.list.mockReturnValue(of({ jobs: [projection()], next_offset: null }));
    await TestBed.configureTestingModule({
      imports: [SpeechReconciliationPanelComponent],
      providers: [
        { provide: SpeechReconciliationApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub:5000' }] } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SpeechReconciliationPanelComponent);
    fixture.componentRef.setInput('hubAuthorized', true);
    fixture.componentRef.setInput('pollIntervalMs', 60_000);
    fixture.detectChanges();
  });

  afterEach(() => fixture.destroy());

  it('shows duration, factor, stage, all budget dimensions, checkpoints and conflict counts without content', () => {
    const text = fixture.nativeElement.textContent;
    for (const value of [
      '00:01:30', '10×', 'alignment', 'Checkpoints', 'Allocated', 'Reserved', 'Consumed', 'Remaining',
      'Gelöst', 'Ungelöst', 'Abgelehnt', 'Quarantäne',
    ]) expect(text).toContain(value);
    expect(text).toContain('Audio- und Transkriptinhalte werden hier nicht geladen');
    expect(text).not.toContain('active_attempt_id');
  });

  it('fences duplicate mutations and applies only the matching Hub response', () => {
    const pending = new Subject<SpeechReconciliationJobView>();
    api.pause.mockReturnValue(pending);

    fixture.componentInstance.run('pause');
    fixture.componentInstance.run('pause');
    expect(api.pause).toHaveBeenCalledTimes(1);
    expect(api.pause.mock.calls[0][2]).toBe(7);
    expect(api.pause.mock.calls[0][3]).toMatch(/^speech-reconciliation-pause-7-/);

    pending.next(projection({ state: 'paused', version: 8 }));
    fixture.detectChanges();
    expect(fixture.componentInstance.selected).toMatchObject({ state: 'paused', version: 8 });
    expect(fixture.nativeElement.textContent).toContain('Hub-Status: paused');
  });

  it('supports reduce, pause, resume and cancel only for valid authoritative states', () => {
    api.reduce.mockReturnValue(of(projection({ max_compute_factor: 5, version: 8 })));
    fixture.componentInstance.reduceFactor = 5;
    fixture.componentInstance.reduce();
    expect(api.reduce).toHaveBeenCalledWith(
      'http://hub:5000', 'speech-reconciliation-job-1', 7, 5, expect.any(String),
    );

    fixture.componentInstance.selected = projection({ state: 'paused' });
    expect(fixture.componentInstance.canPause()).toBe(false);
    expect(fixture.componentInstance.canResume()).toBe(true);
    expect(fixture.componentInstance.canCancel()).toBe(true);
    fixture.componentInstance.selected = projection({ state: 'completed' });
    expect(fixture.componentInstance.canCancel()).toBe(false);
  });

  it('cancels and ignores an in-flight response as soon as Hub authorization is revoked', () => {
    const pending = new Subject<SpeechReconciliationJobView>();
    api.cancel.mockReturnValue(pending);
    fixture.componentInstance.run('cancel');

    fixture.componentRef.setInput('hubAuthorized', false);
    fixture.detectChanges();
    pending.next(projection({ state: 'cancel_requested', version: 8 }));

    expect(fixture.componentInstance.selected).toBeNull();
    expect(fixture.componentInstance.jobs).toHaveLength(0);
  });

  it('never calls the Hub when the reconciliation capability is not authoritative', () => {
    fixture.destroy();
    api.list.mockClear();
    fixture = TestBed.createComponent(SpeechReconciliationPanelComponent);
    fixture.componentRef.setInput('hubAuthorized', false);
    fixture.detectChanges();
    expect(api.list).not.toHaveBeenCalled();
  });
});

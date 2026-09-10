import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EMPTY, NEVER, Subject, of, throwError } from 'rxjs';

import { AgentApiService } from '../services/agent-api.service';
import { PiBackendStatusComponent } from './pi-backend-status.component';
import { piWorkerResponse } from './pi-backend-status.fixture';

describe('Pi Backend status card', () => {
  let fixture: ComponentFixture<PiBackendStatusComponent>;
  const api = { sgptBackendProvision: vi.fn() };
  const worker = { name: 'Worker <script>private</script>', url: 'http://worker:5000', status: 'online' };

  beforeEach(async () => {
    api.sgptBackendProvision.mockReset().mockReturnValue(of(piWorkerResponse()));
    await TestBed.configureTestingModule({
      imports: [PiBackendStatusComponent], providers: [{ provide: AgentApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(PiBackendStatusComponent);
  });
  afterEach(() => { fixture.destroy(); TestBed.resetTestingModule(); vi.useRealTimers(); });

  function render(workers = [worker], hubUrl = 'https://hub.example') {
    fixture.componentRef.setInput('hubUrl', hubUrl);
    fixture.componentRef.setInput('workers', workers);
    fixture.detectChanges();
  }

  it('renders escaped closed status, queries only the Hub and never starts an inference/install', () => {
    render();
    expect(api.sgptBackendProvision).toHaveBeenCalledExactlyOnceWith('https://hub.example', 'pi', {
      worker_url: worker.url, action: 'status',
    });
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('Für Hub-Zuweisungen konfiguriert');
    expect(element.textContent).toContain('Anbieter-Anmeldung nicht geprüft');
    expect(element.textContent).toContain('Inferenzkosten hängen vom Anbieter ab');
    expect(element.textContent).not.toContain('must-not-render');
    expect(element.querySelector('script')).toBeNull();
    expect(element.querySelectorAll('input,select').length).toBe(0);
    expect(fixture.componentInstance.busy()).toBe(false);
  });

  it('does not probe offline Workers or an absent Hub', () => {
    render([{ ...worker, status: 'offline' }]);
    expect(api.sgptBackendProvision).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('Worker nicht online');
    render([worker], '');
    expect(api.sgptBackendProvision).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('Kein Hub verfügbar');
  });

  it('discards old responses when the Hub changes', () => {
    const old = new Subject<unknown>();
    api.sgptBackendProvision.mockReturnValueOnce(old).mockReturnValue(of({}));
    render();
    expect(old.observed).toBe(true);
    render([worker], 'https://other-hub.example');
    expect(old.observed).toBe(false);
    old.next(piWorkerResponse());
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Status nicht verifiziert');
    expect(fixture.nativeElement.textContent).not.toContain('Für Hub-Zuweisungen konfiguriert');
  });

  it('cancels pending observations on destruction', () => {
    const pending = new Subject<unknown>();
    api.sgptBackendProvision.mockReturnValue(pending);
    render();
    fixture.destroy();
    expect(pending.observed).toBe(false);
  });

  it('finishes an empty observation as unverified instead of leaving a loading label', () => {
    api.sgptBackendProvision.mockReturnValue(EMPTY);
    render();
    expect(fixture.componentInstance.busy()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Status nicht verifiziert');
    expect(fixture.nativeElement.textContent).not.toContain('Status wird abgefragt');
  });

  it('bounds silent requests without waiting for a person', () => {
    vi.useFakeTimers();
    api.sgptBackendProvision.mockReturnValue(NEVER);
    render();
    vi.advanceTimersByTime(15001);
    fixture.detectChanges();
    expect(fixture.componentInstance.busy()).toBe(false);
    expect(fixture.nativeElement.textContent).toContain('Statusabfrage fehlgeschlagen');
  });

  it('does not render private error bodies or keep a previous ready status', () => {
    render();
    api.sgptBackendProvision.mockReturnValue(throwError(() => new Error('must-not-render token secret')));
    (fixture.nativeElement.querySelector('button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Statusabfrage fehlgeschlagen');
    expect(fixture.nativeElement.textContent).not.toContain('must-not-render');
    expect(fixture.nativeElement.textContent).not.toContain('Für Hub-Zuweisungen konfiguriert');
  });

  it('deduplicates Workers and bounds concurrency and total fan-out', () => {
    api.sgptBackendProvision.mockReturnValue(NEVER);
    render(Array.from({ length: 40 }, (_, index) => ({ ...worker, url: `http://worker-${index}:5000` })));
    expect(api.sgptBackendProvision).toHaveBeenCalledTimes(4);
    expect(fixture.componentInstance.rows().length).toBe(32);
    expect(fixture.nativeElement.textContent).toContain('32 unterschiedliche Worker');
    api.sgptBackendProvision.mockClear();
    render([worker, worker]);
    expect(api.sgptBackendProvision).toHaveBeenCalledTimes(1);
    expect(fixture.componentInstance.rows().length).toBe(1);
  });
});

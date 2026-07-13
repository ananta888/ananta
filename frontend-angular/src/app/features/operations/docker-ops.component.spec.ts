import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { OpsApiClient } from '../../services/ops-api.client';
import { DockerOpsComponent } from './docker-ops.component';

describe('DockerOpsComponent', () => {
  const api = {
    getDockerStatus: vi.fn(() => of({ available: true, boundary: 'hub_cli', docker_version: '28', compose_available: true, platform_hint: '' })),
    getDockerInfo: vi.fn(() => of({ name: 'docker-host', cpus: 8, memory_bytes: 1024 })),
    listDockerContainers: vi.fn(() => of({ items: [{ id: 'hub-id', name: 'ananta-hub', image: 'ananta:dev', status: 'Up', state: 'running', registered: true, allowed_actions: ['stop', 'restart'] }], count: 1 })),
    listDockerImages: vi.fn(() => of({ items: [], count: 0 })),
    listDockerNetworks: vi.fn(() => of({ items: [], count: 0 })),
    listDockerVolumes: vi.fn(() => of({ items: [], count: 0 })),
    getDockerDiskUsage: vi.fn(() => of({})),
    inspectDockerContainer: vi.fn(() => of({ id: 'hub-id', inspect: { restart_policy: { Name: 'unless-stopped' } } })),
    getDockerContainerStats: vi.fn(() => of({ id: 'hub-id', cpu_percent: '1.5%', memory_usage: '50MiB / 2GiB' })),
    getDockerContainerLogs: vi.fn(() => of({ ok: true, logs: 'ready', stderr: 'warning' })),
    runDockerContainerAction: vi.fn(() => of({ ok: true, action: 'restart' })),
  };

  beforeEach(() => TestBed.configureTestingModule({ imports: [DockerOpsComponent], providers: [{ provide: OpsApiClient, useValue: api }] }));

  it('renders registered containers and loads sanitized inspect plus stats', () => {
    const fixture = TestBed.createComponent(DockerOpsComponent);
    fixture.componentInstance.baseUrl = 'http://hub';
    fixture.componentInstance.containers = [{ id: 'hub-id', name: 'ananta-hub', image: 'ananta:dev', status: 'Up', state: 'running', registered: true, allowed_actions: ['stop', 'restart'] }];
    fixture.componentInstance.selectedContainer = fixture.componentInstance.containers[0];
    fixture.componentInstance.details = { id: 'hub-id', name: 'ananta-hub', image: 'ananta:dev', status: 'Up', inspect: { restart_policy: { Name: 'unless-stopped' } } };
    fixture.componentInstance.stats = { id: 'hub-id', cpu_percent: '1.5%', memory_usage: '50MiB / 2GiB' };
    fixture.detectChanges();
    fixture.componentInstance.loadContainerDetails();

    expect(api.inspectDockerContainer).toHaveBeenCalledWith('http://hub', 'hub-id');
    expect(fixture.nativeElement.textContent).toContain('ananta-hub');
    expect(fixture.nativeElement.textContent).toContain('1.5 %');
  });

  it('does not enable mutations for unregistered containers', () => {
    const fixture = TestBed.createComponent(DockerOpsComponent);
    fixture.componentInstance.selectedContainer = { id: 'foreign', name: 'foreign', image: 'x', status: 'Up', registered: false, allowed_actions: ['stop'] };
    expect(fixture.componentInstance.actionAllowed('stop')).toBe(false);
  });

  it('keeps the configured boundary visible when the engine is disabled', () => {
    const fixture = TestBed.createComponent(DockerOpsComponent);
    const status = (fixture.componentInstance as any).errorEngine({ error: { data: {
      available: false,
      boundary: 'disabled',
      docker_version: '',
      compose_available: false,
      platform_hint: 'Enable the explicit Ops overlay',
      error: { code: 'docker_boundary_not_configured', message: 'Docker Ops boundary is disabled' },
    } } });

    expect(status.boundary).toBe('disabled');
    expect(status.error.code).toBe('docker_boundary_not_configured');
  });
});

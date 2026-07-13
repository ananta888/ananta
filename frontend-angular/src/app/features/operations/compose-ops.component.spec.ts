import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { OpsApiClient } from '../../services/ops-api.client';
import { ComposeOpsComponent } from './compose-ops.component';

describe('ComposeOpsComponent', () => {
  const project = { project_id: 'dev', name: 'Ananta Dev', project_directory: '/repo/docker', compose_files: ['/repo/docker/compose.yml'], profiles: ['dev'], available_profiles: ['dev', 'gpu'], marker: 'preferred', category: 'dev', allowed_actions: ['up', 'stop', 'restart', 'down'], services: [{ name: 'hub', state: 'running', health: 'healthy' }] };
  const api = {
    listComposeProjects: vi.fn(() => of({ items: [project], count: 1 })),
    getComposeProjectStatus: vi.fn(() => of(project)),
    getComposeProjectConfig: vi.fn(() => of({ ok: true, config: 'services:\n  hub:' })),
    getComposeProjectLogs: vi.fn(() => of({ ok: true, logs: 'hub ready', stderr: '' })),
    runComposeProjectAction: vi.fn(() => of({ ok: true, action: 'restart' })),
  };

  beforeEach(() => TestBed.configureTestingModule({ imports: [ComposeOpsComponent], providers: [{ provide: OpsApiClient, useValue: api }] }));

  it('shows registered profiles and live services', () => {
    const fixture = TestBed.createComponent(ComposeOpsComponent);
    fixture.componentInstance.baseUrl = 'http://hub';
    fixture.componentInstance.projects = [project as any];
    fixture.componentInstance.selectedProject = project as any;
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('dev, gpu');
    expect(text).toContain('healthy');
  });

  it('passes an explicit service target to a service restart', () => {
    const fixture = TestBed.createComponent(ComposeOpsComponent);
    fixture.componentInstance.baseUrl = 'http://hub';
    fixture.componentInstance.selectedProject = project as any;

    fixture.componentInstance.projectAction('restart', 'hub', 'APR-4');

    expect(api.runComposeProjectAction).toHaveBeenCalledWith('http://hub', 'dev', 'restart', 'hub', 'APR-4');
  });
});

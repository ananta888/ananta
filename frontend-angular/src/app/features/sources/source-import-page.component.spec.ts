import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';
import { SourceImportPageComponent } from './source-import-page.component';

describe('SourceImportPageComponent', () => {
  let fixture: ComponentFixture<SourceImportPageComponent>;
  let component: SourceImportPageComponent;

  const api = jasmine.createSpyObj<SourceControlV1GovernanceApiClient>(
    'SourceControlV1GovernanceApiClient',
    ['validateContentAdmission', 'createContentAdmission'],
  );
  const connectionApi = jasmine.createSpyObj<SourceControlV1ApiClient>(
    'SourceControlV1ApiClient',
    ['validateConnection', 'createConnection'],
  );
  const catalog = jasmine.createSpyObj<SourceConnectorCatalogService>(
    'SourceConnectorCatalogService',
    ['loadWorkspaces', 'loadRemotes', 'loadIndexProfiles'],
    {
      capabilities: [
        {
          kind: 'direct_text',
          label: 'Direkttext',
          description: 'Text',
          available: true,
          persistable: true,
        },
        {
          kind: 'open_notebook',
          label: 'Notebook',
          description: 'Notebook',
          available: true,
          persistable: true,
        },
        {
          kind: 'registered_workspace',
          label: 'Workspace',
          description: 'Workspace',
          available: true,
          persistable: false,
          reason: 'read-only',
        },
      ],
    },
  );

  beforeEach(async () => {
    api.validateContentAdmission.calls.reset();
    api.createContentAdmission.calls.reset();
    connectionApi.validateConnection.calls.reset();
    connectionApi.createConnection.calls.reset();
    catalog.loadWorkspaces.and.returnValue(of([]));
    catalog.loadRemotes.and.returnValue(of([]));
    catalog.loadIndexProfiles.and.returnValue(of([]));
    api.validateContentAdmission.and.returnValue(
      of({ valid: true, preview: { digest: 'sha256:preview' } } as never),
    );
    api.createContentAdmission.and.returnValue(
      of({ connection: {}, revision: {}, content: {} } as never),
    );
    connectionApi.validateConnection.and.returnValue(of({ connection: {} } as never));
    connectionApi.createConnection.and.returnValue(of({ connection: {} } as never));

    await TestBed.configureTestingModule({
      imports: [SourceImportPageComponent],
      providers: [
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              data: {},
              paramMap: convertToParamMap({}),
              queryParamMap: convertToParamMap({ projectId: 'project-alpha' }),
              parent: null,
            },
          },
        },
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
        { provide: SourceControlV1ApiClient, useValue: connectionApi },
        { provide: SourceConnectorCatalogService, useValue: catalog },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(SourceImportPageComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('validates and then creates direct text with the exact server admission payload', () => {
    component.displayName.set('Architecture notes');
    component.directContent.set('# Hub-owned source');
    component.mediaType.set('text/markdown');
    component.sensitivity.set('confidential');

    component.submit();

    expect(api.validateContentAdmission).toHaveBeenCalledOnceWith({
      project_id: 'project-alpha',
      source_type: 'direct_text',
      display_name: 'Architecture notes',
      sensitivity: 'confidential',
      content: '# Hub-owned source',
      media_type: 'text/markdown',
      dry_run: true,
    });
    expect(api.createContentAdmission).toHaveBeenCalledOnceWith({
      project_id: 'project-alpha',
      source_type: 'direct_text',
      display_name: 'Architecture notes',
      sensitivity: 'confidential',
      content: '# Hub-owned source',
      media_type: 'text/markdown',
      dry_run: false,
    });
    expect(component.completed()).toBeTrue();
  });

  it('admits only the canonical notebook shape without rewriting metadata', () => {
    component.selectedKind.set('open_notebook');
    component.displayName.set('Runbook');
    component.notebookJson.set(
      JSON.stringify({
        cells: [
          {
            cell_type: 'code',
            source: 'print("ok")',
            outputs: [{ output_type: 'stream', text: 'ok' }],
          },
        ],
      }),
    );

    component.submit();

    expect(api.validateContentAdmission).toHaveBeenCalledWith(
      jasmine.objectContaining({
        project_id: 'project-alpha',
        source_type: 'notebook',
        dry_run: true,
      }),
    );
    expect(api.createContentAdmission).toHaveBeenCalled();

    api.validateContentAdmission.calls.reset();
    component.notebookJson.set(
      JSON.stringify({
        cells: [],
        metadata: { kernelspec: { name: 'invented-browser-profile' } },
      }),
    );
    component.submit();
    expect(api.validateContentAdmission).not.toHaveBeenCalled();
  });

  it('binds a server-listed workspace without URL, path or browser-computed digest', () => {
    component.workspaces.set([
      {
        workspaceId: 'workspace-primary',
        label: 'Primary',
        enabled: true,
        readOnly: true,
      },
    ]);
    component.selectedKind.set('registered_workspace');
    component.displayName.set('Primary workspace');
    component.selectedWorkspaceId.set('workspace-primary');

    component.submit();

    expect(connectionApi.validateConnection).toHaveBeenCalledOnceWith({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-primary',
      display_name: 'Primary workspace',
      sensitivity: 'internal',
      dry_run: true,
    });
    expect(connectionApi.createConnection).toHaveBeenCalledOnceWith({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-primary',
      display_name: 'Primary workspace',
      sensitivity: 'internal',
      dry_run: false,
    });
    const serialized = JSON.stringify(connectionApi.createConnection.calls.mostRecent().args[0]);
    expect(serialized).not.toContain('connection_identity_digest');
    expect(serialized).not.toContain('path');
    expect(serialized).not.toContain('url');
  });

  it('reports a rejected admission without attempting persistence', () => {
    api.validateContentAdmission.and.returnValue(
      throwError(() => new Error('policy rejected')),
    );
    component.displayName.set('Rejected');
    component.directContent.set('secret');

    component.submit();

    expect(api.createContentAdmission).not.toHaveBeenCalled();
    expect(component.submitError()).toContain('nicht validiert');
  });

  it('uses native buttons for keyboard selection and retains visible focus', () => {
    fixture.detectChanges();
    const buttons = fixture.nativeElement.querySelectorAll(
      '.source-card',
    ) as NodeListOf<HTMLButtonElement>;
    expect(buttons.length).toBeGreaterThan(1);
    buttons[1].focus();
    buttons[1].click();

    expect(document.activeElement).toBe(buttons[1]);
    expect(component.selectedKind()).toBe('open_notebook');
  });
});

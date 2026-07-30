import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceConnectorCatalogService } from './source-connector-catalog.service';
import { SourceImportPageComponent } from './source-import-page.component';

describe('SourceImportPageComponent', () => {
  let fixture: ComponentFixture<SourceImportPageComponent>;
  let component: SourceImportPageComponent;

  const validateContentAdmission = vi.fn();
  const createContentAdmission = vi.fn();
  const validateConnection = vi.fn();
  const createConnection = vi.fn();
  const loadWorkspaces = vi.fn();
  const loadRemotes = vi.fn();
  const loadIndexProfiles = vi.fn();
  const api = { validateContentAdmission, createContentAdmission };
  const connectionApi = { validateConnection, createConnection };
  const catalog = {
    loadWorkspaces,
    loadRemotes,
    loadIndexProfiles,
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
        persistable: true,
      },
    ],
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    loadWorkspaces.mockReturnValue(of([]));
    loadRemotes.mockReturnValue(of([]));
    loadIndexProfiles.mockReturnValue(of([]));
    validateContentAdmission.mockReturnValue(
      of({ valid: true, preview: { digest: 'preview' } }),
    );
    createContentAdmission.mockReturnValue(
      of({ connection: {}, revision: {}, content: {} }),
    );
    validateConnection.mockReturnValue(of({ connection: {} }));
    createConnection.mockReturnValue(of({ connection: {} }));

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

    const request = {
      project_id: 'project-alpha',
      source_type: 'direct_text',
      display_name: 'Architecture notes',
      sensitivity: 'confidential',
      content: '# Hub-owned source',
      media_type: 'text/markdown',
    };
    expect(validateContentAdmission).toHaveBeenCalledTimes(1);
    expect(validateContentAdmission).toHaveBeenCalledWith(request);
    expect(createContentAdmission).toHaveBeenCalledWith(
      request,
      expect.stringMatching(/^ui:content:create:/),
    );
    expect(component.completed()).toBeTruthy();
  });

  it('admits only the canonical notebook shape without rewriting metadata', () => {
    component.selectedKind.set('open_notebook');
    component.displayName.set('Runbook');
    component.notebookJson.set(JSON.stringify({
      cells: [{
        cell_type: 'code',
        source: 'print("ok")',
        outputs: [{ output_type: 'stream', text: 'ok' }],
      }],
    }));

    component.submit();

    expect(validateContentAdmission).toHaveBeenCalledWith(
      expect.objectContaining({
        project_id: 'project-alpha',
        source_type: 'notebook',
      }),
    );
    expect(createContentAdmission).toHaveBeenCalledTimes(1);

    validateContentAdmission.mockClear();
    component.notebookJson.set(JSON.stringify({
      cells: [],
      metadata: { kernelspec: { name: 'invented-browser-profile' } },
    }));
    component.submit();
    expect(validateContentAdmission).not.toHaveBeenCalled();
  });

  it('binds a server-listed workspace without URL, path or browser-computed digest', () => {
    component.workspaces.set([{
      workspaceId: 'workspace-primary',
      label: 'Primary',
      enabled: true,
      readOnly: true,
    }]);
    component.selectedKind.set('registered_workspace');
    component.displayName.set('Primary workspace');
    component.selectedWorkspaceId.set('workspace-primary');

    component.submit();

    const intent = {
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-primary',
      display_name: 'Primary workspace',
      sensitivity: 'internal',
    };
    expect(validateConnection).toHaveBeenCalledWith(intent);
    expect(createConnection).toHaveBeenCalledWith(
      intent,
      expect.stringMatching(/^ui:connection:create:/),
    );
    const serialized = JSON.stringify(createConnection.mock.calls[0]?.[0]);
    expect(serialized).not.toContain('connection_identity_digest');
    expect(serialized).not.toContain('path');
    expect(serialized).not.toContain('url');
  });

  it('reports a rejected admission without attempting persistence', () => {
    validateContentAdmission.mockReturnValue(
      throwError(() => new Error('policy rejected')),
    );
    component.displayName.set('Rejected');
    component.directContent.set('secret');

    component.submit();

    expect(createContentAdmission).not.toHaveBeenCalled();
    expect(component.submitError()).toContain('nicht validiert');
  });

  it('uses native buttons for keyboard selection and retains visible focus', () => {
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

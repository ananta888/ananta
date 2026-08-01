import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { WorkspaceRegistrationComponent } from './workspace-registration.component';

describe('WorkspaceRegistrationComponent', () => {
  let fixture: ComponentFixture<WorkspaceRegistrationComponent>;
  let component: WorkspaceRegistrationComponent;
  const listWorkspaceFolders = vi.fn();
  const validateWorkspaceFolder = vi.fn();
  const createWorkspaceRegistration = vi.fn();
  const api = {
    listWorkspaceFolders,
    validateWorkspaceFolder,
    createWorkspaceRegistration,
  };
  const folder = {
    folder_handle: 'folder-selection-primary',
    display_name: 'Team Source',
    capabilities: {
      selection_only: true,
      read_only: true,
      path_exposed: false,
      file_names_exposed: false,
      folder_label_exposed: true,
    },
  };
  const validation = {
    validation_handle: 'workspace-validation-primary',
    expires_at_epoch: 2_000_000_000,
    capabilities: {
      selection_only: true,
      read_only: true,
      path_exposed: false,
      file_names_exposed: false,
      folder_label_exposed: true,
    },
  };
  const registration = {
    workspace_id: 'workspace-primary',
    state: 'active' as const,
    read_only: true,
    etag: '"workspace-v1:1"',
    capabilities: { refresh: true },
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    listWorkspaceFolders.mockReturnValue(of({ items: [folder] }));
    validateWorkspaceFolder.mockReturnValue(of(validation));
    createWorkspaceRegistration.mockReturnValue(of(registration));
    await TestBed.configureTestingModule({
      imports: [WorkspaceRegistrationComponent],
      providers: [
        { provide: SourceControlV1GovernanceApiClient, useValue: api },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(WorkspaceRegistrationComponent);
    component = fixture.componentInstance;
    component.projectId = 'project-alpha';
    fixture.detectChanges();
  });

  it('shows only the server label and the read-only disclosure', () => {
    component.selectFolder('folder-selection-primary');
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Team Source');
    expect(text).toContain('read-only');
    expect(text).not.toContain('folder-selection-primary');
    expect(fixture.nativeElement.querySelector('input[type="file"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('input')).toBeNull();
  });

  it('validates the selected opaque handle and creates only from the returned handle', () => {
    const emitted = vi.fn();
    component.workspaceCreated.subscribe(emitted);
    component.selectFolder('folder-selection-primary');

    component.submit();

    expect(validateWorkspaceFolder).toHaveBeenCalledWith(
      'project-alpha',
      'folder-selection-primary',
    );
    expect(createWorkspaceRegistration).toHaveBeenCalledWith(
      'project-alpha',
      'workspace-validation-primary',
      expect.stringMatching(/^ui:workspace:create:/),
    );
    expect(JSON.stringify(validateWorkspaceFolder.mock.calls[0])).not.toContain('path');
    expect(JSON.stringify(validateWorkspaceFolder.mock.calls[0])).not.toContain('file');
    expect(JSON.stringify(validateWorkspaceFolder.mock.calls[0])).not.toContain('upload');
    expect(emitted).toHaveBeenCalledWith(registration);
  });

  it('fails closed without a route project context', () => {
    vi.clearAllMocks();
    component.projectId = '';

    component.reload();

    expect(listWorkspaceFolders).not.toHaveBeenCalled();
    expect(validateWorkspaceFolder).not.toHaveBeenCalled();
    expect(createWorkspaceRegistration).not.toHaveBeenCalled();
    expect(component.canSubmit()).toBeFalsy();
  });

  it('fails closed with an accessible error when labels cannot be loaded', () => {
    listWorkspaceFolders.mockReturnValue(
      throwError(() => new Error('catalog unavailable')),
    );

    component.reload();
    fixture.detectChanges();

    const alert = fixture.nativeElement.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('nicht sicher geladen');
    expect(component.folders()).toEqual([]);
    expect(component.canSubmit()).toBeFalsy();
  });
});

import { HttpEventType, HttpResponse } from '@angular/common/http';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { ProjectContextService } from '../../services/project-context.service';
import { WorkspaceSnapshotApiClient } from '../../services/workspace-snapshot-api.client';
import {
  WorkspaceSnapshotUploadComponent,
  validateWorkspaceSnapshotSelection,
} from './workspace-snapshot-upload.component';

function folderFile(path: string, contents = 'content'): File {
  const file = new File([contents], path.split('/').at(-1) || 'file');
  Object.defineProperty(file, 'webkitRelativePath', { value: path });
  return file;
}

describe('WorkspaceSnapshotUploadComponent', () => {
  it('rejects case-colliding relative paths before transport', () => {
    const result = validateWorkspaceSnapshotSelection([
      folderFile('Folder/src/File.ts'),
      folderFile('folder/SRC/file.ts'),
    ]);

    expect(result.files).toEqual([]);
    expect(result.error).toContain('Groß-/Kleinschreibung');
  });

  it('rejects empty files and control directories before transport', () => {
    const empty = validateWorkspaceSnapshotSelection([
      folderFile('Workspace/src/empty.txt', ''),
    ]);
    const controlDirectory = validateWorkspaceSnapshotSelection([
      folderFile('Workspace/.git/config'),
    ]);

    expect(empty.error).toContain('Leere Dateien');
    expect(controlDirectory.error).toContain('Steuerverzeichnisse');
  });

  it('uploads with progress and emits the registered workspace', () => {
    const api = {
      upload: vi.fn(() => of(
        { type: HttpEventType.UploadProgress, loaded: 3, total: 7 },
        new HttpResponse({
          body: {
            workspace_id: 'workspace-1',
            state: 'active',
            file_count: 1,
            total_bytes: 7,
            replayed: false,
          },
        }),
      )),
    };
    TestBed.configureTestingModule({
      imports: [WorkspaceSnapshotUploadComponent],
      providers: [
        { provide: WorkspaceSnapshotApiClient, useValue: api },
        {
          provide: ProjectContextService,
          useValue: { selectedProjectId: signal('project-1') },
        },
      ],
    });
    const fixture = TestBed.createComponent(WorkspaceSnapshotUploadComponent);
    const component = fixture.componentInstance;
    const emitted = vi.fn();
    component.workspaceCreated.subscribe(emitted);

    const file = folderFile('Workspace/src/main.ts', 'content');
    component.onFolderSelected({ target: { files: [file] } } as unknown as Event);
    component.upload();

    expect(api.upload).toHaveBeenCalledWith(expect.objectContaining({
      projectId: 'project-1',
      displayName: 'Workspace',
      files: [{ file, relativePath: 'Workspace/src/main.ts' }],
    }));
    expect(component.progress()).toBe(100);
    expect(emitted).toHaveBeenCalledWith(expect.objectContaining({ workspace_id: 'workspace-1' }));
  });

  it('fails closed without a selected project', () => {
    const api = { upload: vi.fn() };
    TestBed.configureTestingModule({
      imports: [WorkspaceSnapshotUploadComponent],
      providers: [
        { provide: WorkspaceSnapshotApiClient, useValue: api },
        {
          provide: ProjectContextService,
          useValue: { selectedProjectId: signal<string | null>(null) },
        },
      ],
    });
    const component = TestBed.createComponent(WorkspaceSnapshotUploadComponent).componentInstance;
    component.onFolderSelected({
      target: { files: [folderFile('Workspace/src/main.ts')] },
    } as unknown as Event);

    component.upload();

    expect(api.upload).not.toHaveBeenCalled();
    expect(component.errorMessage()).toContain('Projektkontext');
  });
});

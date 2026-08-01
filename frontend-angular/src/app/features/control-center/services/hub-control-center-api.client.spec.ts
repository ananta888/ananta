import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { HubControlCenterApiClient } from './hub-control-center-api.client';

describe('HubControlCenterApiClient project contract', () => {
  it('normalizes the live data.project create envelope at the API boundary', async () => {
    const project = {
      id: 'server-project-id',
      name: 'Live project',
      description: null,
      status: 'active' as const,
      is_active: true,
    };
    const post = vi.fn(() => of({ project }));
    TestBed.configureTestingModule({
      providers: [
        HubControlCenterApiClient,
        { provide: HubApiCoreService, useValue: { post } },
      ],
    });

    const result = await firstValueFrom(
      TestBed.inject(HubControlCenterApiClient).createProject(
        'http://hub:5000',
        { name: 'Live project' },
      ),
    );

    expect(result).toEqual(project);
    expect(post).toHaveBeenCalledWith(
      'http://hub:5000/api/projects',
      { name: 'Live project' },
      'http://hub:5000',
      undefined,
      false,
    );
  });
});

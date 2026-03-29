import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiService } from '../../services/hub-api.service';
import { AdminFacade } from './admin.facade';

describe('AdminFacade', () => {
  let facade: AdminFacade;
  let hubApi: Record<string, ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    hubApi = {
      listTemplates: vi.fn(() => of([])),
      listArtifacts: vi.fn(() => of([])),
      listTeams: vi.fn(() => of([])),
      createTemplate: vi.fn(() => of({ id: 'tpl-1' })),
      uploadArtifact: vi.fn(() => of({ artifact: { id: 'artifact-1' } })),
      createTeam: vi.fn(() => of({ id: 'team-1' })),
    };

    TestBed.configureTestingModule({
      providers: [
        AdminFacade,
        { provide: HubApiService, useValue: hubApi },
      ],
    });
    facade = TestBed.inject(AdminFacade);
  });

  it('delegates admin listing operations', () => {
    facade.listTemplates('http://hub:5000').subscribe();
    facade.listArtifacts('http://hub:5000').subscribe();
    facade.listTeams('http://hub:5000').subscribe();

    expect(hubApi.listTemplates).toHaveBeenCalledWith('http://hub:5000', undefined);
    expect(hubApi.listArtifacts).toHaveBeenCalledWith('http://hub:5000', undefined);
    expect(hubApi.listTeams).toHaveBeenCalledWith('http://hub:5000', undefined);
  });

  it('delegates admin mutation operations', () => {
    const file = new File(['x'], 'notes.txt', { type: 'text/plain' });
    facade.createTemplate('http://hub:5000', { name: 'Template' }).subscribe();
    facade.uploadArtifact('http://hub:5000', file, 'docs').subscribe();
    facade.createTeam('http://hub:5000', { name: 'Platform' }).subscribe();

    expect(hubApi.createTemplate).toHaveBeenCalledWith('http://hub:5000', { name: 'Template' }, undefined);
    expect(hubApi.uploadArtifact).toHaveBeenCalledWith('http://hub:5000', file, 'docs', undefined);
    expect(hubApi.createTeam).toHaveBeenCalledWith('http://hub:5000', { name: 'Platform' }, undefined);
  });
});

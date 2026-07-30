import { TestBed } from '@angular/core/testing';

import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlCenterFacade } from './source-control-center.facade';

describe('SourceControlCenterFacade', () => {
  it('is composed against the Source Control v1 client without legacy source doubles', () => {
    TestBed.configureTestingModule({
      providers: [
        SourceControlCenterFacade,
        {
          provide: SourceControlV1ApiClient,
          useValue: jasmine.createSpyObj('SourceControlV1ApiClient', [
            'listConnections',
            'listConnectionRevisions',
          ]),
        },
      ],
    });

    expect(TestBed.inject(SourceControlCenterFacade)).toBeTruthy();
  });
});

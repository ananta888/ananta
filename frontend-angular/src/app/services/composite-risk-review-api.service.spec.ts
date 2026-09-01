import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import {
  COMPOSITE_RISK_REVIEW_WARNING,
  CompositeRiskReviewApiService,
} from './composite-risk-review-api.service';

describe('CompositeRiskReviewApiService', () => {
  it('adds the explicit headless request marker', () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        CompositeRiskReviewApiService,
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] },
        },
      ],
    });
    const service = TestBed.inject(CompositeRiskReviewApiService);
    const http = TestBed.inject(HttpTestingController);
    let warning = '';

    service.review({ goal: 'goal' }).subscribe(result => warning = result.warning_text);
    const request = http.expectOne('http://hub.test/api/security/composite-risk-review');
    expect(request.request.body).toEqual({ goal: 'goal', explicit_request: true });
    request.flush({
      data: {
        schema: 'composite_risk_review.v1',
        review_only: true,
        risk_level: 'low',
        indicators: [],
        explanation: 'Keine Indikatoren.',
        recommended_action: 'log_review_hint',
        warning_text: COMPOSITE_RISK_REVIEW_WARNING,
      },
    });
    expect(warning).toBe(COMPOSITE_RISK_REVIEW_WARNING);
    http.verify();
  });
});

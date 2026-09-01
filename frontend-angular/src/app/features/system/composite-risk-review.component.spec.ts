import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import {
  COMPOSITE_RISK_REVIEW_WARNING,
  CompositeRiskReviewApiService,
  CompositeRiskReviewResult,
} from '../../services/composite-risk-review-api.service';
import { CompositeRiskReviewComponent } from './composite-risk-review.component';

const result: CompositeRiskReviewResult = {
  schema: 'composite_risk_review.v1',
  review_only: true,
  risk_level: 'medium',
  indicators: [{
    id: 'sudden_scope_shift',
    description: 'Scope changed',
    severity: 'medium',
    matched_evidence: [],
  }],
  explanation: 'Ein Indikator wurde gefunden.',
  recommended_action: 'automated_independent_review',
  warning_text: COMPOSITE_RISK_REVIEW_WARNING,
};

describe('CompositeRiskReviewComponent', () => {
  it('runs headlessly through the API and never presents an allow/deny status', () => {
    const api = { review: vi.fn(() => of(result)) };
    TestBed.configureTestingModule({
      imports: [CompositeRiskReviewComponent],
      providers: [{ provide: CompositeRiskReviewApiService, useValue: api }],
    });
    const fixture = TestBed.createComponent(CompositeRiskReviewComponent);
    const component = fixture.componentInstance;
    component.payloadText = JSON.stringify({ goal: 'review', tasks: [] });

    component.runReview();
    fixture.detectChanges();

    expect(api.review).toHaveBeenCalledWith({ goal: 'review', tasks: [] });
    expect(component.result).toEqual(result);
    expect(fixture.nativeElement.textContent).toContain(COMPOSITE_RISK_REVIEW_WARNING);
    expect(fixture.nativeElement.textContent).not.toContain('Freigabe erteilt');
  });

  it('rejects invalid JSON without API or interactive input', () => {
    const api = { review: vi.fn(() => of(result)) };
    TestBed.configureTestingModule({
      imports: [CompositeRiskReviewComponent],
      providers: [{ provide: CompositeRiskReviewApiService, useValue: api }],
    });
    const component = TestBed.createComponent(CompositeRiskReviewComponent).componentInstance;
    component.payloadText = '{';

    component.runReview();

    expect(api.review).not.toHaveBeenCalled();
    expect(component.error).toContain('JSON');
  });
});

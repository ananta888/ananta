import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { CollaborationFlowProjection, CollaborationResourceOffer } from './collaboration-workspace.models';
import { CollaborationProjectionPanelsComponent } from './collaboration-projection-panels.component';

describe('CollaborationProjectionPanelsComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [CollaborationProjectionPanelsComponent] }).compileComponents();
  });

  it('renders read-only domain, provenance and terminal resource projections', () => {
    const fixture = TestBed.createComponent(CollaborationProjectionPanelsComponent);
    const flow: CollaborationFlowProjection = {
      schema: 'ananta.collaboration-flow-projection.v1',
      workspace_id: 'workspace-a',
      checkpoint: 9,
      state_digest: 'a'.repeat(64),
      writes_authoritative_state: false,
      worker_invoked: false,
      state: {
        tasks: {
          'task-a': {
            status: 'running',
            codecompass: { graph_digest: 'b'.repeat(64), source_id: 'SRC_REGISTERED' },
          },
        },
        workflows: { 'workflow-a': { status: 'active' } },
        git_refs: { 'main': { head_sha: 'c'.repeat(40) } },
        reviews: { 'review-a': { state: 'approved' } },
        artifacts: {
          'artifact-a': { digest: 'd'.repeat(64), version: '3', scope: 'workspace', availability: 'complete' },
        },
      },
    };
    const offers: CollaborationResourceOffer[] = [{
      schema: 'ananta.collaboration-resource-offer.v1',
      offer_id: 'offer-a',
      workspace_id: 'workspace-a',
      owner_actor_binding_id: 'worker-a',
      resource_id: 'terminal-a',
      capability_category: 'terminal',
      capacity_class: 'medium',
      scopes: ['task:execute'],
      expires_at: 9999999999,
      sensitivity: 'workspace',
      attestation_status: 'verified',
      metadata: {},
      payload_digest: 'e'.repeat(64),
    }];
    fixture.componentRef.setInput('flow', flow);
    fixture.componentRef.setInput('offers', offers);

    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Task · task-a');
    expect(text).toContain('workflow-a');
    expect(text).toContain('Artifact · artifact-a');
    expect(text).toContain('SRC_REGISTERED');
    expect(text).toContain('terminal-a · medium');
    expect(fixture.nativeElement.querySelector('button')).toBeNull();
  });
});

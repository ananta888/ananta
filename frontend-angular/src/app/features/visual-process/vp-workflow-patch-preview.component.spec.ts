import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { VpWorkflowPatchPreviewComponent } from './vp-workflow-patch-preview.component';

describe('VpWorkflowPatchPreviewComponent', () => {
  it('shows an atomic diff and requires explicit confirmation before apply', async () => {
    await TestBed.configureTestingModule({ imports: [VpWorkflowPatchPreviewComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpWorkflowPatchPreviewComponent);
    const component = fixture.componentInstance;
    component.originalGraph = { id: 'g', name: 'G', description: '', version: '1', tags: [], steps: [], edges: [] };
    component.patch = {
      contract_version: 'ananta.visual_process.workflow_patch.v1', graph_id: 'g', definition_revision: 1,
      base_graph_hash: 'a'.repeat(64), evidence_refs: [], extensions: {}, operations: [{
        operation_id: 'op-1', op: 'add_step', temp_id: 'new-step', value: {}, evidence_refs: [],
      }],
    };
    component.preview = {
      patch_hash: 'patch', base_graph_hash: 'a'.repeat(64), preview_graph_hash: 'b'.repeat(64),
      preview_graph: {
        ...component.originalGraph,
        steps: [{ id: 'new-step', label: 'New', kind: 'review', gate: false, policy_hints: [], io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 } }],
      },
      validation: { valid: true, error_count: 0, warning_count: 0, issues: [] }, operation_count: 1,
      audit_id: 'audit', decision: 'previewed',
    };
    const accepted = vi.fn(); component.accepted.subscribe(accepted);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('Node hinzugefügt: new-step');
    expect(element.querySelector('[data-testid="vp-patch-policy"]')?.textContent).toContain('previewed');
    let apply = Array.from(element.querySelectorAll('button')).find(button => button.textContent?.includes('Bestätigt'))!;
    expect(apply.disabled).toBe(true);
    (element.querySelector(`#${component.confirmationId}`) as HTMLInputElement).click();
    await fixture.whenStable(); fixture.detectChanges();
    expect(component.confirmed).toBe(true);
    apply = Array.from(element.querySelectorAll('button')).find(button => button.textContent?.includes('Bestätigt'))!;
    expect(apply.disabled).toBe(false);
    apply.click();
    expect(accepted).toHaveBeenCalledTimes(1);
  });

  it('uses instance-local dialog and confirmation IDs', async () => {
    await TestBed.configureTestingModule({ imports: [VpWorkflowPatchPreviewComponent] }).compileComponents();
    const first = TestBed.createComponent(VpWorkflowPatchPreviewComponent).componentInstance;
    const second = TestBed.createComponent(VpWorkflowPatchPreviewComponent).componentInstance;
    expect(first.confirmationId).not.toBe(second.confirmationId);
    expect(first.titleId).not.toBe(second.titleId);
  });

  it('offers one-click Hub refresh only for a conflict and never enables the stale patch', async () => {
    await TestBed.configureTestingModule({ imports: [VpWorkflowPatchPreviewComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpWorkflowPatchPreviewComponent);
    const component = fixture.componentInstance;
    component.originalGraph = { id: 'g', name: 'Unverändert', description: '', version: '1', tags: [], steps: [], edges: [] };
    component.patch = {
      contract_version: 'ananta.visual_process.workflow_patch.v1', graph_id: 'g', definition_revision: 1,
      base_graph_hash: 'a'.repeat(64), evidence_refs: [], extensions: {}, operations: [{
        operation_id: 'op-1', op: 'add_step', temp_id: 'new-step', value: {}, evidence_refs: [],
      }],
    };
    component.preview = {
      patch_hash: 'patch', base_graph_hash: 'a'.repeat(64), preview_graph_hash: 'b'.repeat(64),
      preview_graph: component.originalGraph,
      validation: { valid: true, error_count: 0, warning_count: 0, issues: [] }, operation_count: 1,
      audit_id: 'audit', decision: 'previewed',
    };
    component.status = 'conflict';
    component.errorCode = 'assistant_patch_draft_changed_after_preview';
    const refreshed = vi.fn(); component.refreshRequested.subscribe(refreshed);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    const refresh = element.querySelector<HTMLButtonElement>('[data-testid="vp-patch-refresh"]')!;
    expect(refresh.textContent).toContain('Neuen Hub-Patch');
    expect(component.canApply()).toBe(false);
    expect((element.querySelector(`#${component.confirmationId}`) as HTMLInputElement).disabled).toBe(true);
    refresh.click();
    expect(refreshed).toHaveBeenCalledTimes(1);
    expect(component.originalGraph.name).toBe('Unverändert');
  });

  it('blocks invalid, rejected and read-only patches and redacts inline secret values', async () => {
    await TestBed.configureTestingModule({ imports: [VpWorkflowPatchPreviewComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpWorkflowPatchPreviewComponent);
    const component = fixture.componentInstance;
    component.originalGraph = { id: 'g', name: 'G', description: '', version: '1', tags: [], steps: [], edges: [] };
    component.patch = {
      contract_version: 'ananta.visual_process.workflow_patch.v1', graph_id: 'g', definition_revision: 1,
      base_graph_hash: 'a'.repeat(64), evidence_refs: [], extensions: {}, operations: [{
        operation_id: 'op-1', op: 'update_step_field', step_id: 'step', path: '/metadata/config',
        expected_old_value: {}, value: { api_key: 'must-not-render', api_key_secret_ref: 'env://KEY' }, evidence_refs: [],
      }],
    };
    component.preview = {
      patch_hash: 'patch', base_graph_hash: 'a'.repeat(64), preview_graph_hash: 'b'.repeat(64),
      preview_graph: component.originalGraph,
      validation: { valid: false, error_count: 1, warning_count: 0, issues: [{ severity: 'error', code: 'invalid', message: 'Ungültig', path: '/steps/step' }] },
      operation_count: 1, audit_id: 'audit', decision: 'rejected',
    };
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('[REDACTED]');
    expect(element.textContent).toContain('env://KEY');
    expect(element.textContent).not.toContain('must-not-render');
    expect(component.canApply()).toBe(false);
    expect((element.querySelector(`#${component.confirmationId}`) as HTMLInputElement).disabled).toBe(true);
    component.preview = { ...component.preview, validation: { valid: true, error_count: 0, warning_count: 0, issues: [] }, decision: 'previewed' };
    component.readOnly = true;
    fixture.detectChanges();
    expect(component.canApply()).toBe(false);
  });
});

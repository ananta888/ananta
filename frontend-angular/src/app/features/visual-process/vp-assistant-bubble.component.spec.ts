import { SimpleChange } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import {
  VpAssistantBubbleComponent,
  VpAssistantRect,
  chooseVpAssistantPlacement,
} from './vp-assistant-bubble.component';

const response = {
  summary: 'Kurz', location: 'Node A', explanation: 'Lang', options: [], warnings: [], next_actions: [], evidence: [],
};

describe('VpAssistantBubbleComponent', () => {
  it('evaluates all four anchor sides and chooses the one with the least node overlap', () => {
    const rect = (left: number, top: number, width: number, height: number): VpAssistantRect => ({
      left, top, width, height, right: left + width, bottom: top + height,
    });
    const placement = chooseVpAssistantPlacement({
      container: rect(0, 0, 1000, 800),
      target: rect(450, 350, 100, 80),
      bubbleWidth: 200,
      bubbleHeight: 120,
      obstacles: [
        rect(560, 320, 240, 180),
        rect(450, 440, 240, 180),
        rect(450, 200, 240, 140),
      ],
    });
    expect(placement.side).toBe('left');
    expect(placement.overlapArea).toBe(0);
  });

  it('is a separate accessible dialog, submits questions and handles Escape', async () => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpAssistantBubbleComponent);
    const component = fixture.componentInstance;
    component.visible = true; component.mode = 'expanded'; component.response = response;
    const asked = vi.fn(); const modeChanged = vi.fn();
    component.questionAsked.subscribe(asked); component.modeChange.subscribe(modeChanged);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toContain('AI-Snake Hilfe');
    expect(element.querySelector('button[aria-label="Hilfe schließen"]')).not.toBeNull();
    component.question = 'Was macht dieser Node?';
    fixture.detectChanges();
    element.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    expect(asked).toHaveBeenCalledWith('Was macht dieser Node?');
    element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(modeChanged).toHaveBeenCalledWith('compact');
  });

  it('uses an instance-local question input ID', async () => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const first = TestBed.createComponent(VpAssistantBubbleComponent);
    first.componentInstance.visible = true; first.componentInstance.mode = 'expanded'; first.componentInstance.response = response;
    first.detectChanges();
    const second = TestBed.createComponent(VpAssistantBubbleComponent);
    second.componentInstance.visible = true; second.componentInstance.mode = 'expanded'; second.componentInstance.response = response;
    second.detectChanges();
    expect((first.nativeElement as HTMLElement).querySelector('input')!.id)
      .not.toBe((second.nativeElement as HTMLElement).querySelector('input')!.id);
  });

  it('traps Tab in expanded mode and restores focus when closed', async () => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const origin = document.createElement('button');
    document.body.append(origin);
    origin.focus();
    const fixture = TestBed.createComponent(VpAssistantBubbleComponent);
    const component = fixture.componentInstance;
    component.visible = true; component.mode = 'expanded'; component.response = response;
    component.ngOnChanges({
      visible: new SimpleChange(false, true, true),
      mode: new SimpleChange('compact', 'expanded', false),
    });
    fixture.detectChanges();
    await new Promise(resolve => setTimeout(resolve));
    const focusable = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled])'));
    const first = focusable[0];
    const last = focusable.at(-1)!;
    last.focus();
    const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    component.trapFocus(tab);
    expect(document.activeElement).toBe(first);
    first.focus();
    const shiftTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    component.trapFocus(shiftTab);
    expect(document.activeElement).toBe(last);
    component.requestClose();
    await Promise.resolve();
    expect(document.activeElement).toBe(origin);
    origin.remove();
  });

  it('moves a pinned bubble within its instance container', async () => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpAssistantBubbleComponent);
    const component = fixture.componentInstance;
    component.visible = true; component.mode = 'pinned'; component.response = response;
    fixture.detectChanges();
    const parent = (fixture.nativeElement as HTMLElement).parentElement!;
    vi.spyOn(parent, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, x: 0, y: 0, toJSON: () => ({}),
    });
    const bubble = (fixture.nativeElement as HTMLElement).querySelector('.vp-assistant-bubble') as HTMLElement;
    vi.spyOn(bubble, 'getBoundingClientRect').mockReturnValue({
      left: 100, top: 100, right: 500, bottom: 400, width: 400, height: 300, x: 100, y: 100, toJSON: () => ({}),
    });
    component.hostLeft = 100; component.hostTop = 100;
    component.startDrag({
      button: 0, pointerId: 7, clientX: 100, clientY: 100,
      preventDefault: vi.fn(), currentTarget: bubble,
    } as unknown as PointerEvent);
    component.dragMove({ pointerId: 7, clientX: 170, clientY: 150 } as PointerEvent);
    expect(component.hostLeft).toBe(170);
    expect(component.hostTop).toBe(150);
  });

  it('offers no patch action in read-only mode', async () => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpAssistantBubbleComponent);
    fixture.componentInstance.visible = true;
    fixture.componentInstance.mode = 'expanded';
    fixture.componentInstance.patchAllowed = false;
    fixture.componentInstance.response = {
      ...response,
      workflow_patch: {
        contract_version: 'ananta.visual_process.workflow_patch.v1', graph_id: 'graph', definition_revision: 1,
        base_graph_hash: 'hash', operations: [], evidence_refs: [], extensions: {},
      },
    };
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('keine Übernahmeaktion');
    expect(Array.from(element.querySelectorAll('button')).some(button => button.textContent?.includes('Vorschau'))).toBe(false);
  });

  it('ships bounded responsive and reduced-motion styles for a 360x800 viewport', () => {
    const styles = ((VpAssistantBubbleComponent as unknown as { ɵcmp: { styles: string[] } }).ɵcmp.styles ?? []).join('\n');
    expect(styles).toContain('clamp(360px,42vw,640px)');
    expect(styles).toContain('max-height:min(70vh,620px)');
    expect(styles).toContain('@media (prefers-reduced-motion:reduce)');
    expect(styles).toContain('@media(max-width:600px)');
  });

  it.each([
    ['failed', 'assistant_context_stale', 'stale', 'Kontext ist veraltet'],
    ['failed', 'assistant_graph_revision_conflict', 'conflict', 'Kontextkonflikt'],
    ['rejected', 'assistant_policy_rejected', 'rejected', 'Anfrage abgelehnt'],
    ['completed', 'assistant_no_results', 'no_results', 'Keine belegbaren Ergebnisse'],
    ['timeout', 'assistant_request_timeout', 'timeout', 'Zeitlimit überschritten'],
    ['cancelled', 'assistant_request_cancelled', 'cancelled', 'Anfrage abgebrochen'],
  ] as const)('renders %s/%s as a distinct explained non-current state', async (status, code, outcome, label) => {
    await TestBed.configureTestingModule({ imports: [VpAssistantBubbleComponent] }).compileComponents();
    const fixture = TestBed.createComponent(VpAssistantBubbleComponent);
    const component = fixture.componentInstance;
    component.visible = true;
    component.mode = 'expanded';
    component.requestStatus = status;
    component.errorCode = code;
    component.patchAllowed = false;
    component.response = {
      ...response,
      workflow_patch: {
        contract_version: 'ananta.visual_process.workflow_patch.v1', graph_id: 'graph', definition_revision: 1,
        base_graph_hash: 'hash', operations: [], evidence_refs: [], extensions: {},
      },
    };
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector(`[data-outcome="${outcome}"]`)?.textContent).toContain(label);
    expect(element.textContent).not.toContain(code);
    expect(Array.from(element.querySelectorAll('button')).some(button => button.textContent?.includes('Vorschau'))).toBe(false);
  });
});

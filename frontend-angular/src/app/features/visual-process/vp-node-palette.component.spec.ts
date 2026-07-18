import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { VpNodeDefinitionRegistryService } from './vp-node-definition-registry.service';
import { VpNodePaletteComponent } from './vp-node-palette.component';

describe('VpNodePaletteComponent', () => {
  it('searches registry metadata, disables non-executable kinds and supports arrow navigation', async () => {
    await TestBed.configureTestingModule({ imports: [VpNodePaletteComponent] }).compileComponents();
    const registry = new VpNodeDefinitionRegistryService();
    const fixture = TestBed.createComponent(VpNodePaletteComponent);
    fixture.componentInstance.graphId = 'graph';
    fixture.componentInstance.definitions = [
      registry.definition({
        id: 'searchable', label: 'Suche', group: 'Retrieval', description: 'Findet Symbole', dispatch_capable: true,
        implementation_state: 'wired_and_executable', implementation_status: 'production', side_effects: ['index_read'],
      }),
      registry.definition({
        id: 'future', label: 'Zukunft', group: 'Retrieval', description: 'Noch nicht aktiv', dispatch_capable: false,
        implementation_state: 'registered_only', implementation_status: 'design_only',
      }),
    ];
    fixture.detectChanges();

    const buttons = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.vp-palette-item'));
    expect(buttons).toHaveLength(2);
    expect(buttons[0].disabled).toBe(false);
    expect(buttons[1].disabled).toBe(true);
    expect(buttons[0].textContent).toContain('index_read');

    const search = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('.vp-palette-search')!;
    search.focus();
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(document.activeElement).toBe(buttons[0]);

    fixture.componentInstance.query = 'index_read';
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.vp-palette-item')).toHaveLength(1);
  });

  it('keeps form and description IDs unique across parallel palettes', async () => {
    await TestBed.configureTestingModule({ imports: [VpNodePaletteComponent] }).compileComponents();
    const first = TestBed.createComponent(VpNodePaletteComponent);
    const second = TestBed.createComponent(VpNodePaletteComponent);
    first.componentInstance.graphId = 'a'; first.componentInstance.definitions = [];
    second.componentInstance.graphId = 'b'; second.componentInstance.definitions = [];
    first.detectChanges(); second.detectChanges();
    const firstId = (first.nativeElement as HTMLElement).querySelector('input')!.id;
    const secondId = (second.nativeElement as HTMLElement).querySelector('input')!.id;
    expect(firstId).not.toBe(secondId);
  });
});

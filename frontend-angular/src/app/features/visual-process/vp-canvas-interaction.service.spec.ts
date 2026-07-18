import { describe, expect, it, vi } from 'vitest';

import { VpCanvasInteractionService } from './vp-canvas-interaction.service';

function host(left: number, top: number): HTMLElement {
  const element = document.createElement('div');
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
    left, top, right: left + 500, bottom: top + 300, width: 500, height: 300,
    x: left, y: top, toJSON: () => ({}),
  });
  return element;
}

describe('VpCanvasInteractionService', () => {
  it('uses only the host bound to the current editor instance', () => {
    const first = new VpCanvasInteractionService();
    const second = new VpCanvasInteractionService();
    const firstHost = host(100, 50);
    const secondHost = host(0, 0);
    first.bindCanvas(firstHost);
    second.bindCanvas(secondHost);

    expect(first.clientToSvg(140, 90)).toEqual({ svgX: 20, svgY: 20 });
    expect(second.clientToSvg(140, 90)).toEqual({ svgX: 120, svgY: 70 });
    expect(first.viewportAnchor()).toEqual({ x: 20, y: 20 });

    first.unbindCanvas(firstHost);
    expect(first.clientToSvg(140, 90)).toEqual({ svgX: 140, svgY: 90 });
    expect(second.clientToSvg(140, 90)).toEqual({ svgX: 120, svgY: 70 });
  });
});

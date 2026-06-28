import { CodehugCanvasInteractionService } from './codehug-canvas-interaction.service';

describe('CodehugCanvasInteractionService', () => {
  let service: CodehugCanvasInteractionService;

  beforeEach(() => {
    service = new CodehugCanvasInteractionService();
  });

  describe('view transform', () => {
    it('starts with default pan and scale', () => {
      expect(service.viewTx()).toBe(40);
      expect(service.viewTy()).toBe(20);
      expect(service.viewScale()).toBe(1);
    });

    it('reset() restores default pan and scale', () => {
      service.viewTx.set(200);
      service.viewTy.set(150);
      service.viewScale.set(2);
      service.reset();
      expect(service.viewTx()).toBe(40);
      expect(service.viewTy()).toBe(20);
      expect(service.viewScale()).toBe(1);
    });
  });

  describe('registerSvgElement + center signals', () => {
    function makeSvg(width: number, height: number): SVGSVGElement {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      // jsdom: clientWidth/clientHeight are 0 by default — patch them.
      Object.defineProperty(svg, 'clientWidth', { value: width, configurable: true });
      Object.defineProperty(svg, 'clientHeight', { value: height, configurable: true });
      return svg;
    }

    it('returns NaN before any svg element is registered', () => {
      expect(Number.isNaN(service.centerX())).toBe(true);
      expect(Number.isNaN(service.centerY())).toBe(true);
    });

    it('exposes canvas-space center of the registered svg element', () => {
      const svg = makeSvg(800, 600);
      service.registerSvgElement(svg);
      expect(service.centerX()).toBe((800 / 2 - 40) / 1);
      expect(service.centerY()).toBe((600 / 2 - 20) / 1);
    });

    it('reflects pan and scale changes', () => {
      const svg = makeSvg(800, 600);
      service.registerSvgElement(svg);
      service.viewTx.set(100);
      service.viewTy.set(50);
      service.viewScale.set(2);
      expect(service.centerX()).toBe((800 / 2 - 100) / 2);
      expect(service.centerY()).toBe((600 / 2 - 50) / 2);
    });

    it('unregisters svg when null is passed', () => {
      const svg = makeSvg(800, 600);
      service.registerSvgElement(svg);
      service.registerSvgElement(null);
      expect(Number.isNaN(service.centerX())).toBe(true);
      expect(Number.isNaN(service.centerY())).toBe(true);
    });
  });
});
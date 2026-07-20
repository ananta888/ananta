import { ChangeDetectionStrategy, Component, ElementRef, Input, ViewChild } from '@angular/core';
import {
  SemanticRendererInput,
  SemanticRendererMeasurement,
  SemanticRendererPort,
} from '../../services/semantic-reconstruction.service';

@Component({
  selector: 'app-semantic-stream-renderer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <figure aria-label="Rekonstruierter semantischer Bildstream">
      <canvas #surface [attr.aria-busy]="rendering"></canvas>
      <figcaption>{{ fallbackReason || ('Sequenz ' + lastSequence) }}</figcaption>
    </figure>
  `,
  styles: [`
    :host { display: block; }
    canvas { width: 100%; background: #111; border-radius: .4rem; }
    figcaption { color: #aeb4c0; font-size: .8rem; }
  `],
})
export class SemanticStreamRendererComponent implements SemanticRendererPort {
  @ViewChild('surface', { static: true }) surface?: ElementRef<HTMLCanvasElement>;
  @Input() fallbackReason = '';
  rendering = false;
  lastSequence = 0;

  async render(input: Readonly<SemanticRendererInput>, signal: AbortSignal): Promise<SemanticRendererMeasurement> {
    if (signal.aborted) throw new DOMException('aborted', 'AbortError');
    const canvas = this.surface?.nativeElement;
    if (!canvas) throw new Error('renderer_surface_missing');
    this.rendering = true;
    const started = performance.now();
    let bitmap: ImageBitmap | undefined;
    try {
      bitmap = await createImageBitmap(input.encodedBlob);
      if (signal.aborted) throw new DOMException('aborted', 'AbortError');
      canvas.width = bitmap.width; canvas.height = bitmap.height;
      const context = canvas.getContext('2d', { alpha: false });
      if (!context) throw new Error('renderer_context_missing');
      context.drawImage(bitmap, 0, 0);
      this.lastSequence = input.sequence;
      return Object.freeze({
        renderMs: Math.max(0, performance.now() - started),
        workingBytes: input.encodedBlob.size + (input.baseReferenceBlob?.size ?? 0),
        driftScore: 0, staleRegions: 0, qualityScore: 1,
      });
    } finally {
      bitmap?.close();
      this.rendering = false;
    }
  }
}

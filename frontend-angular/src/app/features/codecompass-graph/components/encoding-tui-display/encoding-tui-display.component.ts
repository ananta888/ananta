// TQ-020: TUI/text display for Quantization and TransformerFeatureProvider
import {
  Component, Input, ChangeDetectionStrategy, computed, signal, OnChanges,
} from '@angular/core';
import { VectorEncodingDiagnostics } from '../vector-encoding-status/vector-encoding-status.component';

export interface EncodingTuiDiagnostics {
  vector_encoding?: VectorEncodingDiagnostics;
  transformer_feature_mode?: string;
}

@Component({
  standalone: true,
  selector: 'app-encoding-tui-display',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<pre class="tui-readout">{{ text() }}</pre>`,
  styles: [`
    .tui-readout {
      font-family: ui-monospace, 'Cascadia Code', 'Fira Mono', monospace;
      font-size: 0.75rem;
      color: #94a3b8;
      margin: 0;
      padding: 0;
      background: transparent;
      border: none;
      line-height: 1.4;
      white-space: pre;
    }
  `],
})
export class EncodingTuiDisplayComponent implements OnChanges {
  @Input() diagnostics: EncodingTuiDiagnostics | null = null;

  private readonly _diag = signal<EncodingTuiDiagnostics | null>(null);

  readonly text = computed(() => {
    const d = this._diag();

    const vecLine = this._vectorEncodingLine(d?.vector_encoding);
    const tfLine  = this._transformerLine(d?.transformer_feature_mode);

    return `${vecLine}\n${tfLine}`;
  });

  ngOnChanges(): void {
    this._diag.set(this.diagnostics);
  }

  private _vectorEncodingLine(enc: VectorEncodingDiagnostics | undefined): string {
    if (!enc || !enc.enabled || enc.mode === 'off') {
      return 'VectorEncoding: —';
    }

    let parts = enc.mode;

    if (enc.compression_ratio != null) {
      parts += ` [${enc.compression_ratio.toFixed(1)}×]`;
    }

    if (enc.max_abs_error != null) {
      parts += ` err=${enc.max_abs_error.toFixed(3)}`;
    }

    if (enc.experimental) {
      parts += ' (exp)';
    }

    return `VectorEncoding: ${parts}`;
  }

  private _transformerLine(mode: string | undefined): string {
    if (!mode || mode === 'disabled' || mode === 'off' || mode === '') {
      return 'TransformerFeature: disabled';
    }
    return `TransformerFeature: ${mode}`;
  }
}

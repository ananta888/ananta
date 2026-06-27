// TQ-019: VectorEncodingProfile status component
import {
  Component, Input, ChangeDetectionStrategy, computed, signal, OnChanges,
} from '@angular/core';
import { CommonModule } from '@angular/common';

export interface VectorEncodingDiagnostics {
  mode: string;
  enabled: boolean;
  experimental: boolean;
  profile_hash: string;
  fallback_policy: string;
  compression_ratio?: number | null;
  max_abs_error?: number | null;
}

type ModeBadgeStyle = 'gray' | 'blue' | 'green' | 'orange' | 'red';

interface BadgeConfig {
  color: ModeBadgeStyle;
  label: string;
  experimental: boolean;
}

const MODE_BADGE_MAP: Record<string, BadgeConfig> = {
  off:                              { color: 'gray',   label: 'off',                         experimental: false },
  float32:                          { color: 'gray',   label: 'float32',                     experimental: false },
  float16:                          { color: 'blue',   label: 'float16',                     experimental: false },
  int8:                             { color: 'green',  label: 'int8',                        experimental: false },
  symmetric4bit:                    { color: 'orange', label: 'symmetric4bit',               experimental: false },
  turboquant_mse_experimental:      { color: 'red',    label: 'turboquant_mse_experimental', experimental: true  },
};

function badgeFor(mode: string): BadgeConfig {
  return MODE_BADGE_MAP[mode] ?? { color: 'gray', label: mode, experimental: false };
}

@Component({
  standalone: true,
  selector: 'app-vector-encoding-status',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="vec-card">
      @if (diag() === null || diag()!.mode === 'off') {
        <span class="disabled-hint">Encoding disabled</span>
      } @else {
        <!-- Experimental warning banner -->
        @if (diag()!.experimental) {
          <div class="exp-banner">⚠ Experimental</div>
        }

        <!-- Mode badge -->
        <div class="row">
          <span class="key">Mode</span>
          <span class="badge" [attr.data-color]="badge().color">{{ badge().label }}</span>
          @if (badge().experimental) {
            <span class="exp-tag">EXPERIMENTAL</span>
          }
        </div>

        <!-- Compression ratio -->
        @if (diag()!.compression_ratio !== null && diag()!.compression_ratio !== undefined) {
          <div class="row">
            <span class="key">Ratio</span>
            <span class="val">{{ diag()!.compression_ratio | number:'1.1-1' }}×</span>
          </div>
        }

        <!-- Max abs error -->
        @if (diag()!.max_abs_error !== null && diag()!.max_abs_error !== undefined) {
          <div class="row">
            <span class="key">Max err</span>
            <span class="val">{{ diag()!.max_abs_error | number:'1.3-3' }}</span>
          </div>
        }

        <!-- Fallback policy -->
        <div class="row">
          <span class="key">Fallback</span>
          <span class="val muted">{{ diag()!.fallback_policy }}</span>
        </div>
      }
    </div>
  `,
  styles: [`
    .vec-card {
      width: 200px;
      padding: 6px 8px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 6px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      font-size: 0.75rem;
    }

    .disabled-hint {
      color: #94a3b8;
      font-style: italic;
      font-size: 0.73rem;
    }

    .exp-banner {
      background: #fef2f2;
      border: 1px solid #fca5a5;
      border-radius: 4px;
      color: #b91c1c;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 2px 6px;
      text-align: center;
    }

    .row {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .key {
      color: #64748b;
      min-width: 52px;
      font-size: 0.7rem;
    }

    .val {
      color: #1e293b;
      font-family: ui-monospace, monospace;
      font-size: 0.72rem;
    }

    .val.muted {
      color: #94a3b8;
    }

    .badge {
      font-size: 0.7rem;
      font-family: ui-monospace, monospace;
      padding: 1px 6px;
      border-radius: 3px;
      font-weight: 600;
    }

    .badge[data-color="gray"]   { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
    .badge[data-color="blue"]   { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
    .badge[data-color="green"]  { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
    .badge[data-color="orange"] { background: #fff7ed; color: #c2410c; border: 1px solid #fed7aa; }
    .badge[data-color="red"]    { background: #fef2f2; color: #b91c1c; border: 1px solid #fca5a5; }

    .exp-tag {
      font-size: 0.63rem;
      font-weight: 700;
      color: #b91c1c;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
  `],
})
export class VectorEncodingStatusComponent implements OnChanges {
  @Input() encodingDiagnostics: VectorEncodingDiagnostics | null = null;

  readonly diag = signal<VectorEncodingDiagnostics | null>(null);

  readonly badge = computed(() => {
    const d = this.diag();
    return d ? badgeFor(d.mode) : badgeFor('off');
  });

  ngOnChanges(): void {
    this.diag.set(this.encodingDiagnostics);
  }
}

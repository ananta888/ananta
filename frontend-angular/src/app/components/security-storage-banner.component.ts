import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { SecureTokenStorage } from '../services/secure-token-storage.service';

@Component({
  selector: 'app-security-storage-banner',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (showBanner()) {
      <div class="security-banner" role="alert">
        <strong>Sicherheits-Hinweis:</strong> {{ bannerMessage() }}
      </div>
    }
  `,
  styles: [`
    .security-banner {
      background: #fb7185;
      color: #1a0a0a;
      padding: 8px 12px;
      font-size: 12px;
      border-bottom: 2px solid #b91c1c;
    }
  `],
})
export class SecurityStorageBannerComponent implements OnInit {
  private secureStorage = inject(SecureTokenStorage);

  readonly showBanner = signal(false);
  readonly bannerMessage = signal('');

  async ngOnInit(): Promise<void> {
    const reason = await this.secureStorage.getFallbackReason();
    if (reason) {
      this.bannerMessage.set(reason);
      this.showBanner.set(true);
    }
  }
}

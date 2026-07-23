import { Component, OnInit, inject } from '@angular/core';

import { DashboardFeatureFlagStore } from '../../dashboard-foundation/dashboard-feature-flags';
import { ModelDashboardStore } from './model-dashboard.store';

@Component({
  standalone: true,
  selector: 'app-model-dashboard',
  providers: [ModelDashboardStore],
  template: `
    @if (features.angularModelDashboard()) {
      <section class="model-dashboard" aria-labelledby="model-dashboard-title" data-testid="model-dashboard">
        <header>
          <div>
            <p class="eyebrow">Secret-free Catalog v1</p>
            <h2 id="model-dashboard-title">Modelle und Provider</h2>
            <p>Verfügbarkeit, Laufzeit und Standardauswahl aus der Hub-Projektion.</p>
          </div>
          @if (store.canRefresh()) {
            <button type="button" (click)="store.refresh()" [disabled]="store.loading()">
              Provider aktualisieren
            </button>
          }
        </header>
        @if (store.error()) { <p class="model-error" role="alert">{{ store.error() }}</p> }
        @if (store.loading()) { <p role="status">Katalog wird geladen …</p> }

        @for (failure of store.catalog()?.provider_failures || []; track failure.provider_id) {
          <p class="provider-failure" role="status">
            <strong>{{ failure.provider_id }}</strong> ist isoliert ausgefallen:
            {{ failure.reason_code }}
          </p>
        }

        <div class="provider-grid">
          @for (provider of store.providers(); track provider.providerId) {
            <section class="provider-card" [attr.aria-labelledby]="'provider-' + provider.providerId">
              <h3 [id]="'provider-' + provider.providerId">{{ provider.providerId }}</h3>
              <ul>
                @for (model of provider.models; track model.model_id) {
                  <li>
                    <div>
                      <strong>{{ model.display_name }}</strong>
                      <code>{{ model.model_id }}</code>
                    </div>
                    <div class="model-signals">
                      <span>{{ runtimeLabel(model.runtime) }}</span>
                      <span>{{ availabilityLabel(model.availability) }}</span>
                      <span>{{ healthLabel(model.health) }}</span>
                      @if (model.is_default) { <strong>Standardmodell</strong> }
                    </div>
                    @if (model.capabilities.length) {
                      <p>Fähigkeiten: {{ model.capabilities.join(', ') }}</p>
                    }
                    @if (store.canSetDefault() && !model.is_default) {
                      <button
                        type="button"
                        [disabled]="model.availability !== 'available'"
                        (click)="store.setDefault(model)">
                        Als Standard wählen
                      </button>
                    }
                  </li>
                }
              </ul>
            </section>
          }
        </div>
      </section>
    }
  `,
  styles: [`
    .model-dashboard { display: grid; gap: 1rem; }
    .model-dashboard > header {
      align-items: start; background: linear-gradient(120deg, #173c48, #286773);
      border-radius: 1rem; color: white; display: flex; justify-content: space-between; padding: 1.2rem;
    }
    .eyebrow { font-size: .72rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
    h2 { font-family: Georgia, serif; margin: .2rem 0; }
    .provider-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); }
    .provider-card { border: 1px solid #b8c8c9; border-radius: .8rem; padding: 1rem; }
    .provider-card ul { display: grid; gap: .7rem; list-style: none; padding: 0; }
    .provider-card li { background: #f1f6f4; border-left: 4px solid #286773; padding: .8rem; }
    .provider-card li > div:first-child { display: grid; gap: .2rem; }
    .model-signals { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .5rem; }
    .model-signals span, .model-signals strong {
      border: 1px solid #8da6a7; border-radius: 99rem; font-size: .75rem; padding: .2rem .45rem;
    }
    .provider-failure, .model-error { background: #fff0eb; border-left: 4px solid #9f3029; padding: .7rem; }
    button:focus-visible { outline: 3px solid #1677c8; outline-offset: 2px; }
    @media (max-width: 480px) {
      .model-dashboard > header { flex-direction: column; gap: .8rem; }
      .provider-grid { grid-template-columns: 1fr; }
    }
  `],
})
export class ModelDashboardComponent implements OnInit {
  readonly features = inject(DashboardFeatureFlagStore);
  readonly store = inject(ModelDashboardStore);

  ngOnInit(): void {
    this.features.ensureLoaded().subscribe(flags => {
      if (flags.angularModelDashboard) this.store.load();
    });
  }

  runtimeLabel(value: string): string {
    return `Laufzeit ${value}`;
  }

  availabilityLabel(value: string): string {
    return `Verfügbarkeit ${value}`;
  }

  healthLabel(value: string): string {
    return `Gesundheit ${value}`;
  }
}


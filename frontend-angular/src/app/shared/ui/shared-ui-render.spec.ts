import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { RouterTestingModule } from '@angular/router/testing';
import { EmptyStateComponent } from './state';
import { ExplanationNoticeComponent, NextStepsComponent, SummaryPanelComponent, TableShellComponent } from './display';
import { FormFieldComponent, ModeCardPickerComponent, PresetPickerComponent, WizardShellComponent } from './forms';
import { PageIntroComponent, SectionCardComponent } from './layout';

@Component({
  standalone: true,
  imports: [
    EmptyStateComponent,
    ExplanationNoticeComponent,
    FormFieldComponent,
    ModeCardPickerComponent,
    NextStepsComponent,
    PageIntroComponent,
    PresetPickerComponent,
    SectionCardComponent,
    SummaryPanelComponent,
    TableShellComponent,
    WizardShellComponent,
  ],
  template: `
    <app-page-intro title="Start" subtitle="Intro"></app-page-intro>
    <app-section-card title="Abschnitt" variant="technical">
      <button section-actions>Aktion</button>
      Inhalt
    </app-section-card>
    <app-empty-state title="Leer" description="Keine Daten"></app-empty-state>
    <app-explanation-notice title="Warum" message="Erklaerung" tone="technical"></app-explanation-notice>
    <app-summary-panel title="Summary" [metrics]="[{ label: 'A', value: 1 }]"></app-summary-panel>
    <app-table-shell title="Tabelle" [empty]="true" emptyTitle="Keine Zeilen"></app-table-shell>
    <app-form-field label="Name" hint="Hinweis"><input /></app-form-field>
    <app-mode-card-picker [options]="[{ id: 'a', title: 'A' }]"></app-mode-card-picker>
    <app-preset-picker [presets]="[{ id: 'p', title: 'Preset' }]"></app-preset-picker>
    <app-wizard-shell [steps]="[{ id: 'one', title: 'One' }]"></app-wizard-shell>
    <app-next-steps [steps]="[{ id: 'n', label: 'Weiter' }]"></app-next-steps>
  `,
})
class SharedUiRenderHostComponent {}

describe('shared ui render guardrails', () => {
  it('renders representative shared primitives together', async () => {
    await TestBed.configureTestingModule({
      imports: [RouterTestingModule, SharedUiRenderHostComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(SharedUiRenderHostComponent);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Start');
    expect(text).toContain('Abschnitt');
    expect(text).toContain('Keine Zeilen');
    expect(text).toContain('Weiter');
  });
});

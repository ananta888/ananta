import { Injectable } from '@angular/core';
import { MarkdownSlideDiagnostic, MarkdownSlideTheme } from './markdown-slide.models';

const THEMES: MarkdownSlideTheme[] = [
  {
    id: 'ananta-default',
    label: 'Ananta Default',
    background: '#f8fafc',
    foreground: '#102033',
    accent: '#0369a1',
    codeBackground: '#e8eef5',
    slidePadding: '42px',
  },
  {
    id: 'dark-console',
    label: 'Dark Console',
    background: '#101319',
    foreground: '#e5e7eb',
    accent: '#34d399',
    codeBackground: '#05070b',
    slidePadding: '42px',
  },
  {
    id: 'clean-docs',
    label: 'Clean Docs',
    background: '#ffffff',
    foreground: '#1f2937',
    accent: '#7c3aed',
    codeBackground: '#f3f4f6',
    slidePadding: '48px',
  },
];

@Injectable({ providedIn: 'root' })
export class MarkdownSlideThemeService {
  readonly themes = THEMES;

  resolve(themeId?: string): { theme: MarkdownSlideTheme; diagnostic?: MarkdownSlideDiagnostic } {
    const requested = String(themeId || '').trim() || 'ananta-default';
    const theme = THEMES.find(item => item.id === requested);
    if (theme) return { theme };
    return {
      theme: THEMES[0],
      diagnostic: {
        severity: 'warning',
        code: 'unknown_theme',
        message: `Unknown theme '${requested}', using ananta-default.`,
      },
    };
  }
}

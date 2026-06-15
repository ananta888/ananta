/**
 * UiSnapshotService — converts the current DOM state to a compact text
 * representation that weak text-only models can understand.
 *
 * Output example (< 500 chars):
 *   /teams | nav:Dashboard|Chats|Teams*|Board | tab:Blueprints*|Mitglieder |
 *   h:Teams & Blueprints | list:3 | focus:input[Name]="My Team" | err:Fehler XY
 *
 * Format rules:
 *   * = active / selected / current
 *   [disabled] = disabled control
 *   [open] = open dialog/dropdown
 */
import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class UiSnapshotService {

  capture(): string {
    const parts: string[] = [];
    try {
      parts.push(location.pathname);
      const nav = this.navItems();
      if (nav) parts.push('nav:' + nav);
      const tabs = this.tabItems();
      if (tabs) parts.push('tab:' + tabs);
      const wps = this.waypointItems();
      if (wps) parts.push('wp:' + wps);
      const h = this.heading();
      if (h) parts.push('h:' + h);
      const dlg = this.dialog();
      if (dlg) parts.push('dlg:' + dlg);
      const err = this.errorText();
      if (err) parts.push('err:' + err);
      const cnt = this.listCount();
      if (cnt > 0) parts.push('list:' + cnt);
      const foc = this.focused();
      if (foc) parts.push('focus:' + foc);
    } catch { /* never throw in draw loop */ }
    return parts.join(' | ').slice(0, 450);
  }

  // ── Extractors ──────────────────────────────────────────────────────────────

  private navItems(): string {
    // Angular router sets router-link-active / router-link-exact-active on <a routerLink>
    const navEl = document.querySelector('nav[aria-label="Hauptnavigation"]') ||
                  document.querySelector('.app-nav') ||
                  document.querySelector('nav');
    if (!navEl) return '';
    const links = Array.from(navEl.querySelectorAll('a[data-waypoint]'));
    if (!links.length) return '';
    return links.map(a => {
      const label = (a.textContent || '').trim().slice(0, 18);
      const isActive = a.classList.contains('router-link-exact-active') ||
                       a.classList.contains('router-link-active') ||
                       a.getAttribute('aria-current') === 'page';
      return isActive ? label + '*' : label;
    }).filter(Boolean).join('|');
  }

  private tabItems(): string {
    // Look for elements with class "tab" that are within the visible viewport
    const tabs = Array.from(document.querySelectorAll('.tab, [role="tab"]'))
      .filter(el => this.isVisible(el));
    if (!tabs.length) return '';
    return tabs.map(el => {
      const label = (el.textContent || '').trim().slice(0, 20);
      const isActive = el.classList.contains('active') ||
                       el.getAttribute('aria-selected') === 'true' ||
                       el.getAttribute('aria-current') === 'true';
      const disabled = (el as HTMLButtonElement).disabled ||
                       el.getAttribute('aria-disabled') === 'true';
      return label + (isActive ? '*' : '') + (disabled ? '[off]' : '');
    }).filter(Boolean).join('|');
  }

  private waypointItems(): string {
    // Non-nav, non-tab waypoints (e.g. section markers like teams.blueprint-catalog)
    const all = Array.from(document.querySelectorAll('[data-waypoint]'))
      .filter(el => {
        const wp = el.getAttribute('data-waypoint') || '';
        return !wp.startsWith('nav.') && !wp.startsWith('assistant.') && this.isVisible(el);
      });
    if (!all.length) return '';
    return all.slice(0, 8).map(el => {
      const wp = el.getAttribute('data-waypoint')!;
      const active = el.classList.contains('active') ||
                     el.getAttribute('aria-selected') === 'true';
      const disabled = (el as HTMLButtonElement).disabled;
      return wp + (active ? '*' : '') + (disabled ? '[disabled]' : '');
    }).join('|');
  }

  private heading(): string {
    const h1 = document.querySelector('main h1, [role="main"] h1, .page-title');
    if (h1) return (h1.textContent || '').trim().slice(0, 50);
    const h2 = document.querySelector('main h2, [role="main"] h2');
    if (h2) return (h2.textContent || '').trim().slice(0, 50);
    return '';
  }

  private dialog(): string {
    const dlg = document.querySelector('[role="dialog"]:not([hidden]), [role="alertdialog"]:not([hidden]), .modal.open, .modal.visible, dialog[open]');
    if (!dlg || !this.isVisible(dlg)) return '';
    const titleEl = dlg.querySelector('[role="heading"], h2, h3, .modal-title, .dialog-title');
    return (titleEl?.textContent || dlg.getAttribute('aria-label') || 'dialog').trim().slice(0, 40);
  }

  private errorText(): string {
    // Visible alerts, toasts, error messages
    const sel = '[role="alert"]:not([hidden]), .toast.error, .toast.warning, .alert-error, .error-msg, .form-error';
    const els = Array.from(document.querySelectorAll(sel)).filter(el => this.isVisible(el));
    if (!els.length) return '';
    return els.map(el => (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)).filter(Boolean).join('; ').slice(0, 80);
  }

  private listCount(): number {
    // Count meaningful list items or table rows (excluding headers) in the main area
    const main = document.querySelector('main, [role="main"], .page-content') || document.body;
    const rows = main.querySelectorAll('tbody tr, ul.item-list > li, ol > li, .list-item');
    const visible = Array.from(rows).filter(el => this.isVisible(el));
    return visible.length;
  }

  private focused(): string {
    const el = document.activeElement as HTMLInputElement | null;
    if (!el || el === document.body || el === document.documentElement) return '';
    const tag = el.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') return '';
    const placeholder = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('name') || '';
    const isSensitive = el.type === 'password'
      || /password|token|key|secret/i.test(el.getAttribute('name') ?? '')
      || /password|token|key|secret/i.test(el.getAttribute('autocomplete') ?? '');
    const val = isSensitive ? '[***]' : (el.value || '').slice(0, 40);
    const label = placeholder ? `[${placeholder.slice(0, 20)}]` : '';
    return `${tag}${label}="${val}"`;
  }

  private isVisible(el: Element): boolean {
    try {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.top < window.innerHeight && r.bottom > 0;
    } catch { return false; }
  }
}

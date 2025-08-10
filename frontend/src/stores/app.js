import { defineStore } from 'pinia';

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : null;
}

function setCookie(name, value, days = 365) {
  const d = new Date();
  d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${d.toUTCString()}; path=/`;
}

export const useAppStore = defineStore('app', {
  state: () => ({
    theme: getCookie('theme') || 'light',
    toasts: [] // { id, type: 'info'|'error'|'success', message }
  }),
  actions: {
    applyTheme(theme) {
      const root = document.documentElement;
      root.setAttribute('data-theme', theme);
      document.body.classList.toggle('dark', theme === 'dark');
    },
    async setTheme(theme) {
      this.theme = theme;
      this.applyTheme(theme);
      try {
        const res = await fetch('/set_theme', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme })
        });
        // If server doesn't support, fallback to cookie only
        if (!res.ok) {
          setCookie('theme', theme);
        } else {
          // Try to read Set-Cookie if any; still set our cookie for persistence
          setCookie('theme', theme);
        }
        this.pushToast({ type: 'success', message: `Theme auf "${theme}" gesetzt.` });
      } catch (e) {
        setCookie('theme', theme);
        this.pushToast({ type: 'error', message: 'Theme konnte nicht am Server gespeichert werden. Lokal gesetzt.' });
      }
    },
    initTheme() {
      this.applyTheme(this.theme);
    },
    pushToast({ type = 'info', message }) {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, type, message });
      // auto-dismiss after 4s
      setTimeout(() => this.removeToast(id), 4000);
    },
    removeToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
    }
  }
});

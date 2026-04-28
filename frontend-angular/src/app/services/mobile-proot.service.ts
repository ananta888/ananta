import { Injectable } from '@angular/core';

const DISTRO_STORAGE_KEY = 'ananta.mobile.proot.distro';

@Injectable({ providedIn: 'root' })
export class MobileProotService {
  readonly distroOptions = ['ubuntu', 'debian', 'alpine', 'archlinux', 'fedora', 'opensuse'];

  getSelectedDistro(): string {
    try {
      const value = String(localStorage.getItem(DISTRO_STORAGE_KEY) || '').trim().toLowerCase();
      if (this.distroOptions.includes(value)) return value;
    } catch {}
    return 'ubuntu';
  }

  setSelectedDistro(distro: string): void {
    const normalized = this.normalizeDistro(distro);
    try {
      localStorage.setItem(DISTRO_STORAGE_KEY, normalized);
    } catch {}
  }

  buildCheckCommand(): string {
    return [
      'echo "== proot-distro check =="',
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then "$PROOT_DISTRO_BIN" --version || true; else echo "proot-distro fehlt"; fi',
    ].join(' && ');
  }

  buildListInstalledCommand(): string {
    return [
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then "$PROOT_DISTRO_BIN" list; else echo "proot-distro fehlt"; fi',
    ].join(' && ');
  }

  buildInstallCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    return [
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then',
      `  "$PROOT_DISTRO_BIN" install ${selected};`,
      'else',
      '  echo "proot-distro fehlt im App-Kontext."; exit 1;',
      'fi',
    ].join(' ');
  }

  buildLoginCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    return [
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then',
      `  "$PROOT_DISTRO_BIN" login ${selected};`,
      'else',
      '  echo "proot-distro fehlt im App-Kontext."; exit 1;',
      'fi',
    ].join(' ');
  }

  buildWorkerStartInDistroCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    const workerStart = [
      'cd /data/data/com.termux/files/home/ananta',
      'ROLE=worker AGENT_NAME=android-worker PORT=5001 HUB_URL=http://127.0.0.1:5000 AGENT_URL=http://127.0.0.1:5001 python -m agent.ai_agent',
    ].join(' && ');
    return [
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then',
      `  "$PROOT_DISTRO_BIN" login ${selected} --shared-tmp -- /bin/sh -lc '${workerStart}';`,
      'else',
      '  echo "proot-distro fehlt im App-Kontext."; exit 1;',
      'fi',
    ].join(' ');
  }

  private normalizeDistro(distro: string): string {
    const value = String(distro || '').trim().toLowerCase();
    return this.distroOptions.includes(value) ? value : 'ubuntu';
  }

  private prootResolverSnippet(): string {
    return 'PROOT_DISTRO_BIN="$(command -v proot-distro || true)"; if [ -z "$PROOT_DISTRO_BIN" ] && [ -x /data/data/com.termux/files/usr/bin/proot-distro ]; then PROOT_DISTRO_BIN=/data/data/com.termux/files/usr/bin/proot-distro; fi';
  }
}

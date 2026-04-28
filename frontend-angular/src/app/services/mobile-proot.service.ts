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
    const workerStart = this.workerStartSnippet();
    return [
      this.prootResolverSnippet(),
      'if [ -n "$PROOT_DISTRO_BIN" ]; then',
      `  "$PROOT_DISTRO_BIN" login ${selected} --shared-tmp -- /bin/sh -lc '${workerStart}';`,
      'else',
      '  echo "proot-distro fehlt im App-Kontext."; exit 1;',
      'fi',
    ].join(' ');
  }

  buildWorkerStartCommand(): string {
    return this.workerStartSnippet();
  }

  private normalizeDistro(distro: string): string {
    const value = String(distro || '').trim().toLowerCase();
    return this.distroOptions.includes(value) ? value : 'ubuntu';
  }

  private prootResolverSnippet(): string {
    return 'PROOT_DISTRO_BIN="$(command -v proot-distro || true)"; if [ -z "$PROOT_DISTRO_BIN" ] && [ -x /data/data/com.termux/files/usr/bin/proot-distro ]; then PROOT_DISTRO_BIN=/data/data/com.termux/files/usr/bin/proot-distro; fi';
  }

  private workerStartSnippet(): string {
    return [
      'ANANTA_ROOT=""',
      'for d in /data/data/com.termux/files/home/ananta /data/user/0/com.ananta.mobile/files/ananta /data/data/com.ananta.mobile/files/ananta; do',
      '  if [ -d "$d/agent" ]; then ANANTA_ROOT="$d"; break; fi',
      'done',
      'if [ -z "$ANANTA_ROOT" ]; then',
      '  for d in /data/user/0/com.ananta.mobile/files /data/data/com.ananta.mobile/files /data/data/com.termux/files/home; do',
      '    if [ -d "$d" ]; then ANANTA_ROOT="$d"; break; fi',
      '  done',
      'fi',
      'if [ -z "$ANANTA_ROOT" ]; then echo "Ananta workspace nicht gefunden"; exit 1; fi',
      'cd "$ANANTA_ROOT"',
      'ROLE=worker AGENT_NAME=android-worker PORT=5001 HUB_URL=http://127.0.0.1:5000 AGENT_URL=http://127.0.0.1:5001 python -m agent.ai_agent',
    ].join(' && ');
  }
}

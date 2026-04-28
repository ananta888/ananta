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
      'echo "== ananta proot runtime check =="',
      this.prootResolverSnippet(),
      'if [ -x "$ANANTA_PROOT_BIN" ]; then "$ANANTA_PROOT_BIN" --version || true; else echo "proot runtime fehlt"; fi',
      this.rootfsResolverSnippet(),
      'if [ -d "$ANANTA_ROOTFS" ]; then echo "rootfs vorhanden: $ANANTA_ROOTFS"; else echo "rootfs fehlt fuer distro: $ANANTA_DISTRO"; fi',
    ].join(' && ');
  }

  buildListInstalledCommand(): string {
    return [
      this.runtimeRootResolverSnippet(),
      'if [ -d "$ANANTA_PROOT_RUNTIME/distros" ]; then ls -1 "$ANANTA_PROOT_RUNTIME/distros"; else echo "keine distros installiert"; fi',
    ].join(' && ');
  }

  buildInstallCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    return `echo "Installiere ${selected} ueber nativen Installer-Button (nicht mehr ueber proot-distro shell)."`;
  }

  buildLoginCommand(distro: string): string {
    this.setSelectedDistro(distro);
    return [
      this.prootResolverSnippet(),
      this.rootfsResolverSnippet(),
      'if [ -x "$ANANTA_PROOT_BIN" ] && [ -d "$ANANTA_ROOTFS" ]; then',
      '  export HOME=/root TERM=${TERM:-xterm-256color} PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin;',
      '  "$ANANTA_PROOT_BIN" -0 -r "$ANANTA_ROOTFS" -b /dev -b /proc -b /sys -w /root /bin/sh -l;',
      'else',
      '  echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren.";',
      'fi',
    ].join(' ');
  }

  buildWorkerStartInDistroCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    const workerStart = this.workerStartSnippet();
    return [
      this.prootResolverSnippet(),
      this.rootfsResolverSnippet(selected),
      'if [ -x "$ANANTA_PROOT_BIN" ] && [ -d "$ANANTA_ROOTFS" ]; then',
      `  "$ANANTA_PROOT_BIN" -0 -r "$ANANTA_ROOTFS" -b /dev -b /proc -b /sys -w /root /bin/sh -lc '${workerStart}';`,
      'else',
      '  echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; exit 1;',
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
    return [
      this.runtimeRootResolverSnippet(),
      'ANANTA_PROOT_BIN="$ANANTA_PROOT_RUNTIME/bin/proot"',
      'if [ ! -x "$ANANTA_PROOT_BIN" ]; then ANANTA_PROOT_BIN=""; fi',
    ].join(' && ');
  }

  private runtimeRootResolverSnippet(): string {
    return [
      'ANANTA_PROOT_RUNTIME=""',
      'for d in /data/user/0/com.ananta.mobile/files/proot-runtime /data/data/com.ananta.mobile/files/proot-runtime; do',
      '  if [ -d "$d" ]; then ANANTA_PROOT_RUNTIME="$d"; break; fi',
      'done',
      'if [ -z "$ANANTA_PROOT_RUNTIME" ]; then ANANTA_PROOT_RUNTIME="/data/user/0/com.ananta.mobile/files/proot-runtime"; fi',
    ].join(' && ');
  }

  private rootfsResolverSnippet(selectedDistro?: string): string {
    const distro = this.normalizeDistro(selectedDistro || this.getSelectedDistro());
    return [
      `ANANTA_DISTRO="${distro}"`,
      'ANANTA_ROOTFS="$ANANTA_PROOT_RUNTIME/distros/$ANANTA_DISTRO/rootfs"',
    ].join(' && ');
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

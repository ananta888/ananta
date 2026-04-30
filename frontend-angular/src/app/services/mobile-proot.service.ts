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
      'if [ -f "$ANANTA_PROOT_WRAPPER" ]; then chmod 755 "$ANANTA_PROOT_WRAPPER" 2>/dev/null || true; if [ -n "$ANANTA_PROOT_BIN" ]; then chmod 755 "$ANANTA_PROOT_BIN" 2>/dev/null || true; fi; /system/bin/sh "$ANANTA_PROOT_WRAPPER" --version || true; else echo "proot runtime fehlt"; fi',
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
      this.rootfsResolverSnippet(),
      this.prootResolverSnippet(),
      'if [ -f "$ANANTA_PROOT_WRAPPER" ] && [ -d "$ANANTA_ROOTFS" ]; then chmod 755 "$ANANTA_PROOT_WRAPPER" 2>/dev/null || true; if [ -n "$ANANTA_PROOT_BIN" ]; then chmod 755 "$ANANTA_PROOT_BIN" 2>/dev/null || true; fi; ANANTA_ENV_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin:/system/xbin"; ANANTA_WORKDIR="/"; if [ -d "$ANANTA_ROOTFS/root" ]; then ANANTA_WORKDIR="/root"; fi; ANANTA_LOGIN_SHELL=""; if [ -f "$ANANTA_ROOTFS/bin/sh" ]; then ANANTA_LOGIN_SHELL="/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/sh" ]; then ANANTA_LOGIN_SHELL="/usr/bin/sh"; elif [ -f "$ANANTA_ROOTFS/bin/bash" ]; then ANANTA_LOGIN_SHELL="/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/bash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/dash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/dash" ]; then ANANTA_LOGIN_SHELL="/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/ash" ]; then ANANTA_LOGIN_SHELL="/bin/ash"; fi; if [ -z "$ANANTA_LOGIN_SHELL" ]; then echo "keine Login-Shell-Datei im rootfs gefunden ($ANANTA_ROOTFS)"; exit 1; fi; ANANTA_PROOT_TMP_BASE=""; for t in /data/user/0/com.ananta.mobile/cache "$ANANTA_PROOT_RUNTIME/tmp"; do if [ -d "$t" ] || mkdir -p "$t" 2>/dev/null; then ANANTA_PROOT_TMP_BASE="$t"; break; fi; done; [ -n "$ANANTA_PROOT_TMP_BASE" ] || { echo "kein beschreibbares temp-verzeichnis fuer proot gefunden"; exit 1; }; ANANTA_PROOT_TMP="$ANANTA_PROOT_TMP_BASE/ananta-proot"; mkdir -p "$ANANTA_PROOT_TMP" 2>/dev/null || true; chmod 700 "$ANANTA_PROOT_TMP" 2>/dev/null || true; ANANTA_APK_PATH="$(pm path com.ananta.mobile 2>/dev/null | sed -n \'1s/^package://p\')"; ANANTA_LIB_DIR=""; ANANTA_PROOT_DIRECT=""; if [ -n "$ANANTA_APK_PATH" ]; then ANANTA_LIB_DIR="$(dirname "$ANANTA_APK_PATH")/lib/arm64"; if [ -f "$ANANTA_LIB_DIR/libprootclassic.so" ]; then ANANTA_PROOT_DIRECT="$ANANTA_LIB_DIR/libprootclassic.so"; fi; fi; if [ -z "$ANANTA_PROOT_DIRECT" ]; then echo "proot binary nicht gefunden"; exit 1; fi; ANANTA_PROOT_LOADER=""; if [ -f "$ANANTA_LIB_DIR/libproot-loader.so" ]; then ANANTA_PROOT_LOADER="$ANANTA_LIB_DIR/libproot-loader.so"; fi; echo "proot login shell: $ANANTA_LOGIN_SHELL"; export PROOT_FORCE_KOMPAT=1; export PROOT_TMP_DIR="$ANANTA_PROOT_TMP"; export TMPDIR="$ANANTA_PROOT_TMP"; export PROOT_LOADER="$ANANTA_PROOT_LOADER"; export HOME=/root; export TERM=${TERM:-xterm-256color}; export PATH="$ANANTA_ENV_PATH"; export GLIBC_TUNABLES=glibc.pthread.rseq=0; exec "$ANANTA_PROOT_DIRECT" -r "$ANANTA_ROOTFS" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b "$ANANTA_PROOT_TMP:/tmp" -w "$ANANTA_WORKDIR" "$ANANTA_LOGIN_SHELL" -l; else echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; fi',
    ].join(' && ');
  }

  buildWorkerStartInDistroCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    const workerStart = this.workerStartSnippet();
    return [
      this.rootfsResolverSnippet(selected),
      this.prootResolverSnippet(),
      `if [ -f "$ANANTA_PROOT_WRAPPER" ] && [ -d "$ANANTA_ROOTFS" ]; then chmod 755 "$ANANTA_PROOT_WRAPPER" 2>/dev/null || true; if [ -n "$ANANTA_PROOT_BIN" ]; then chmod 755 "$ANANTA_PROOT_BIN" 2>/dev/null || true; fi; ANANTA_ENV_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin:/system/xbin"; ANANTA_WORKDIR="/"; if [ -d "$ANANTA_ROOTFS/root" ]; then ANANTA_WORKDIR="/root"; fi; ANANTA_LOGIN_SHELL=""; if [ -f "$ANANTA_ROOTFS/bin/sh" ]; then ANANTA_LOGIN_SHELL="/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/sh" ]; then ANANTA_LOGIN_SHELL="/usr/bin/sh"; elif [ -f "$ANANTA_ROOTFS/bin/bash" ]; then ANANTA_LOGIN_SHELL="/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/bash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/dash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/dash" ]; then ANANTA_LOGIN_SHELL="/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/ash" ]; then ANANTA_LOGIN_SHELL="/bin/ash"; fi; if [ -z "$ANANTA_LOGIN_SHELL" ]; then echo "keine Shell-Datei im rootfs gefunden ($ANANTA_ROOTFS)"; exit 1; fi; ANANTA_PROOT_TMP_BASE=""; for t in /data/user/0/com.ananta.mobile/cache "$ANANTA_PROOT_RUNTIME/tmp"; do if [ -d "$t" ] || mkdir -p "$t" 2>/dev/null; then ANANTA_PROOT_TMP_BASE="$t"; break; fi; done; [ -n "$ANANTA_PROOT_TMP_BASE" ] || { echo "kein beschreibbares temp-verzeichnis fuer proot gefunden"; exit 1; }; ANANTA_PROOT_TMP="$ANANTA_PROOT_TMP_BASE/ananta-proot"; mkdir -p "$ANANTA_PROOT_TMP" 2>/dev/null || true; chmod 700 "$ANANTA_PROOT_TMP" 2>/dev/null || true; ANANTA_APK_PATH="$(pm path com.ananta.mobile 2>/dev/null | sed -n '1s/^package://p')"; if [ -n "$ANANTA_APK_PATH" ]; then ANANTA_LIB_DIR="$(dirname "$ANANTA_APK_PATH")/lib/arm64"; if [ -f "$ANANTA_LIB_DIR/libprootclassic.so" ]; then ANANTA_WRAPPER_TARGET="$(sed -n '2s/^exec \"\\([^\"]*\\)\".*/\\1/p' \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null)"; if [ "$ANANTA_WRAPPER_TARGET" != "$ANANTA_LIB_DIR/libprootclassic.so" ]; then printf '#!/system/bin/sh\\nexec "%s" "$@"\\n' "$ANANTA_LIB_DIR/libprootclassic.so" > "$ANANTA_PROOT_WRAPPER"; chmod 755 "$ANANTA_PROOT_WRAPPER" 2>/dev/null || true; fi; fi; fi; PROOT_NO_SECCOMP=1 PROOT_NO_SECCOMP_FILTER=1 PROOT_TMP_DIR="$ANANTA_PROOT_TMP" TMPDIR="$ANANTA_PROOT_TMP" HOME=/root TERM=\${TERM:-xterm-256color} PATH="$ANANTA_ENV_PATH" /system/bin/sh "$ANANTA_PROOT_WRAPPER" -r "$ANANTA_ROOTFS" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b "$ANANTA_PROOT_TMP:/tmp" -w "$ANANTA_WORKDIR" "$ANANTA_LOGIN_SHELL" -c '${workerStart}'; else echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; exit 1; fi`,
    ].join(' && ');
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
      'ANANTA_PROOT_WRAPPER="$ANANTA_PROOT_RUNTIME/bin/proot"',
      'ANANTA_PROOT_BIN="$ANANTA_PROOT_RUNTIME/bin/proot-rs"; if [ ! -f "$ANANTA_PROOT_BIN" ]; then ANANTA_PROOT_BIN=""; fi',
      'ANANTA_EMBEDDED_PROOT=""',
      'ANANTA_APK_PATH="$(pm path com.ananta.mobile 2>/dev/null | sed -n \'1s/^package://p\')"',
      'if [ -n "$ANANTA_APK_PATH" ]; then ANANTA_LIB_DIR="$(dirname "$ANANTA_APK_PATH")/lib/arm64"; if [ -f "$ANANTA_LIB_DIR/libprootrs.so" ]; then ANANTA_EMBEDDED_PROOT="$ANANTA_LIB_DIR/libprootrs.so"; elif [ -f "$ANANTA_LIB_DIR/libprootclassic.so" ]; then ANANTA_EMBEDDED_PROOT="$ANANTA_LIB_DIR/libprootclassic.so"; fi; fi',
      'if [ -n "$ANANTA_EMBEDDED_PROOT" ]; then ANANTA_WRAPPER_TARGET=""; if [ -f "$ANANTA_PROOT_WRAPPER" ]; then ANANTA_WRAPPER_TARGET="$(sed -n \'2s/^exec \"\\([^\"]*\\)\".*/\\1/p\' \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null)"; fi; if [ "$ANANTA_WRAPPER_TARGET" != "$ANANTA_EMBEDDED_PROOT" ]; then mkdir -p "$(dirname "$ANANTA_PROOT_WRAPPER")"; printf \'#!/system/bin/sh\\nexec "%s" "$@"\\n\' "$ANANTA_EMBEDDED_PROOT" > "$ANANTA_PROOT_WRAPPER"; chmod 755 "$ANANTA_PROOT_WRAPPER" 2>/dev/null || true; fi; fi',
    ].join(' && ');
  }

  private runtimeRootResolverSnippet(): string {
    return 'ANANTA_PROOT_RUNTIME=""; for d in /data/user/0/com.ananta.mobile/files/proot-runtime /data/data/com.ananta.mobile/files/proot-runtime; do if [ -d "$d" ]; then ANANTA_PROOT_RUNTIME="$d"; break; fi; done; if [ -z "$ANANTA_PROOT_RUNTIME" ]; then ANANTA_PROOT_RUNTIME="/data/user/0/com.ananta.mobile/files/proot-runtime"; fi';
  }

  private rootfsResolverSnippet(selectedDistro?: string): string {
    const distro = this.normalizeDistro(selectedDistro || this.getSelectedDistro());
    return [
      `ANANTA_DISTRO="${distro}"`,
      'ANANTA_PROOT_RUNTIME=""',
      'for d in /data/user/0/com.ananta.mobile/files/proot-runtime /data/data/com.ananta.mobile/files/proot-runtime; do if [ -f "$d/bin/proot" ] && [ -d "$d/distros/$ANANTA_DISTRO/rootfs" ]; then ANANTA_PROOT_RUNTIME="$d"; break; fi; done',
      'if [ -z "$ANANTA_PROOT_RUNTIME" ]; then for d in /data/user/0/com.ananta.mobile/files/proot-runtime /data/data/com.ananta.mobile/files/proot-runtime; do if [ -d "$d" ]; then ANANTA_PROOT_RUNTIME="$d"; break; fi; done; fi',
      'if [ -z "$ANANTA_PROOT_RUNTIME" ]; then ANANTA_PROOT_RUNTIME="/data/user/0/com.ananta.mobile/files/proot-runtime"; fi',
      'ANANTA_ROOTFS="$ANANTA_PROOT_RUNTIME/distros/$ANANTA_DISTRO/rootfs"',
      'if [ -d "$ANANTA_ROOTFS" ] && [ ! -e "$ANANTA_ROOTFS/bin" ] && [ ! -e "$ANANTA_ROOTFS/usr/bin" ]; then ANANTA_ROOTFS_MATCH=""; for child in "$ANANTA_ROOTFS"/*; do if [ ! -d "$child" ]; then continue; fi; if [ -f "$child/etc/os-release" ] && grep -qi "^ID=$ANANTA_DISTRO$" "$child/etc/os-release" 2>/dev/null; then ANANTA_ROOTFS_MATCH="$child"; break; fi; done; if [ -n "$ANANTA_ROOTFS_MATCH" ]; then ANANTA_ROOTFS="$ANANTA_ROOTFS_MATCH"; else for child in "$ANANTA_ROOTFS"/*; do if [ -d "$child" ] && { [ -e "$child/bin" ] || [ -e "$child/usr/bin" ]; } && [ -f "$child/etc/os-release" ]; then ANANTA_ROOTFS="$child"; break; fi; done; fi; fi',
    ].join(' && ');
  }

  private workerStartSnippet(): string {
    return [
      'ANANTA_ROOT=""',
      'for d in /data/data/com.termux/files/home/ananta /data/user/0/com.ananta.mobile/files/ananta /data/data/com.ananta.mobile/files/ananta; do if [ -d "$d/agent" ]; then ANANTA_ROOT="$d"; break; fi; done',
      'if [ -z "$ANANTA_ROOT" ]; then for d in /data/user/0/com.ananta.mobile/files /data/data/com.ananta.mobile/files /data/data/com.termux/files/home; do if [ -d "$d" ]; then ANANTA_ROOT="$d"; break; fi; done; fi',
      'if [ -z "$ANANTA_ROOT" ]; then echo "Ananta workspace nicht gefunden"; exit 1; fi',
      'cd "$ANANTA_ROOT"',
      'ANANTA_PYTHON=""',
      'if command -v python >/dev/null 2>&1; then ANANTA_PYTHON="python"; elif command -v python3 >/dev/null 2>&1; then ANANTA_PYTHON="python3"; fi',
      'if [ -z "$ANANTA_PYTHON" ]; then echo "Kein Python-Interpreter im Distro gefunden (python/python3)."; exit 1; fi',
      'echo "worker python: $ANANTA_PYTHON"',
      'ROLE=worker AGENT_NAME=android-worker PORT=5001 HUB_URL=http://127.0.0.1:5000 AGENT_URL=http://127.0.0.1:5001 "$ANANTA_PYTHON" -m agent.ai_agent',
    ].join(' && ');
  }
}

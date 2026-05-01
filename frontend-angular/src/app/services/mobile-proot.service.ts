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
      this.runtimeRootResolverSnippet(),
      this.apkProotResolverSnippet(),
      'if [ -n "$ANANTA_PROOT_DIRECT" ]; then echo "proot binary: $ANANTA_PROOT_DIRECT"; LD_LIBRARY_PATH="$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}" "$ANANTA_PROOT_DIRECT" --version 2>/dev/null || echo "proot version check fehlgeschlagen"; else echo "proot runtime fehlt (kein libprootclassic.so in APK)"; fi',
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
      this.apkProotResolverSnippet(),
<<<<<<< HEAD
      'if [ -n "$ANANTA_PROOT_DIRECT" ] && [ -d "$ANANTA_ROOTFS" ]; then ANANTA_ENV_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin:/system/xbin"; ANANTA_WORKDIR="/"; if [ -d "$ANANTA_ROOTFS/root" ]; then ANANTA_WORKDIR="/root"; fi; ANANTA_LOGIN_SHELL=""; ANANTA_SHELL_ARGS=""; if [ -f "$ANANTA_ROOTFS/bin/bash" ]; then ANANTA_LOGIN_SHELL="/bin/bash"; ANANTA_SHELL_ARGS="--noediting --noprofile --norc -i"; elif [ -f "$ANANTA_ROOTFS/usr/bin/bash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/bash"; ANANTA_SHELL_ARGS="--noediting --noprofile --norc -i"; elif [ -f "$ANANTA_ROOTFS/bin/sh" ]; then ANANTA_LOGIN_SHELL="/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/sh" ]; then ANANTA_LOGIN_SHELL="/usr/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/dash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/dash" ]; then ANANTA_LOGIN_SHELL="/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/ash" ]; then ANANTA_LOGIN_SHELL="/bin/ash"; fi; if [ -z "$ANANTA_LOGIN_SHELL" ]; then echo "keine Login-Shell-Datei im rootfs gefunden ($ANANTA_ROOTFS)"; exit 1; fi; ANANTA_PROOT_TMP_BASE=""; for t in /data/user/0/com.ananta.mobile/cache "$ANANTA_PROOT_RUNTIME/tmp"; do if [ -d "$t" ] || mkdir -p "$t" 2>/dev/null; then ANANTA_PROOT_TMP_BASE="$t"; break; fi; done; [ -n "$ANANTA_PROOT_TMP_BASE" ] || { echo "kein beschreibbares temp-verzeichnis fuer proot gefunden"; exit 1; }; ANANTA_PROOT_TMP="$ANANTA_PROOT_TMP_BASE/ananta-proot"; mkdir -p "$ANANTA_PROOT_TMP" 2>/dev/null || true; chmod 700 "$ANANTA_PROOT_TMP" 2>/dev/null || true; echo "proot login shell: $ANANTA_LOGIN_SHELL"; unset PYTHONPATH PYTHONHOME PYTHONDONTWRITEBYTECODE PYTHONSTARTUP; export PROOT_FORCE_KOMPAT=1; export PROOT_TMP_DIR="$ANANTA_PROOT_TMP"; export TMPDIR="$ANANTA_PROOT_TMP"; export HOME=/root; export TERM=${TERM:-xterm-256color}; export PATH="$ANANTA_ENV_PATH"; export LD_LIBRARY_PATH="$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}"; export GLIBC_TUNABLES=glibc.pthread.rseq=0; export PS1="ananta@ubuntu:\\w\\$ "; export http_proxy="${http_proxy:-}"; export https_proxy="${https_proxy:-}"; export HTTP_PROXY="${HTTP_PROXY:-}"; export HTTPS_PROXY="${HTTPS_PROXY:-}"; exec "$ANANTA_PROOT_DIRECT" -0 --link2symlink -r "$ANANTA_ROOTFS" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b "$ANANTA_PROOT_TMP:/tmp" -w "$ANANTA_WORKDIR" "$ANANTA_LOGIN_SHELL" $ANANTA_SHELL_ARGS; else echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; fi',
=======
      'if [ -n "$ANANTA_PROOT_DIRECT" ] && [ -d "$ANANTA_ROOTFS" ]; then ANANTA_ENV_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin:/system/xbin"; ANANTA_WORKDIR="/"; if [ -d "$ANANTA_ROOTFS/root" ]; then ANANTA_WORKDIR="/root"; fi; ANANTA_LOGIN_SHELL=""; ANANTA_SHELL_ARGS=""; if [ -f "$ANANTA_ROOTFS/bin/bash" ]; then ANANTA_LOGIN_SHELL="/bin/bash"; ANANTA_SHELL_ARGS="--noediting --noprofile --norc -i"; elif [ -f "$ANANTA_ROOTFS/usr/bin/bash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/bash"; ANANTA_SHELL_ARGS="--noediting --noprofile --norc -i"; elif [ -f "$ANANTA_ROOTFS/bin/sh" ]; then ANANTA_LOGIN_SHELL="/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/sh" ]; then ANANTA_LOGIN_SHELL="/usr/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/dash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/dash" ]; then ANANTA_LOGIN_SHELL="/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/ash" ]; then ANANTA_LOGIN_SHELL="/bin/ash"; fi; if [ -z "$ANANTA_LOGIN_SHELL" ]; then echo "keine Login-Shell-Datei im rootfs gefunden ($ANANTA_ROOTFS)"; exit 1; fi; ANANTA_PROOT_TMP_BASE=""; for t in /data/user/0/com.ananta.mobile/cache "$ANANTA_PROOT_RUNTIME/tmp"; do if [ -d "$t" ] || mkdir -p "$t" 2>/dev/null; then ANANTA_PROOT_TMP_BASE="$t"; break; fi; done; [ -n "$ANANTA_PROOT_TMP_BASE" ] || { echo "kein beschreibbares temp-verzeichnis fuer proot gefunden"; exit 1; }; ANANTA_PROOT_TMP="$ANANTA_PROOT_TMP_BASE/ananta-proot"; mkdir -p "$ANANTA_PROOT_TMP" 2>/dev/null || true; chmod 700 "$ANANTA_PROOT_TMP" 2>/dev/null || true; echo "proot login shell: $ANANTA_LOGIN_SHELL"; unset PYTHONPATH PYTHONHOME PYTHONDONTWRITEBYTECODE PYTHONSTARTUP; export PROOT_FORCE_KOMPAT=1; export PROOT_TMP_DIR="$ANANTA_PROOT_TMP"; export TMPDIR="$ANANTA_PROOT_TMP"; export HOME=/root; export TERM=${TERM:-xterm-256color}; export PATH="$ANANTA_ENV_PATH"; export LD_LIBRARY_PATH="$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}"; export GLIBC_TUNABLES=glibc.pthread.rseq=0; export PS1="ananta@ubuntu:\\w\\$ "; export http_proxy="${http_proxy:-}"; export https_proxy="${https_proxy:-}"; export HTTP_PROXY="${HTTP_PROXY:-}"; export HTTPS_PROXY="${HTTPS_PROXY:-}"; exec "$ANANTA_PROOT_DIRECT" -r "$ANANTA_ROOTFS" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b "$ANANTA_PROOT_TMP:/tmp" -w "$ANANTA_WORKDIR" "$ANANTA_LOGIN_SHELL" $ANANTA_SHELL_ARGS; else echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; fi',
>>>>>>> dce1235236da1ca11f837c878093b5131a91f000
    ].join(' && ');
  }

  buildWorkerStartInDistroCommand(distro: string): string {
    const selected = this.normalizeDistro(distro);
    const workerStart = this.workerStartSnippet();
    return [
      this.rootfsResolverSnippet(selected),
      this.apkProotResolverSnippet(),
      `if [ -n "$ANANTA_PROOT_DIRECT" ] && [ -d "$ANANTA_ROOTFS" ]; then ANANTA_ENV_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/system/bin:/system/xbin"; ANANTA_WORKDIR="/"; if [ -d "$ANANTA_ROOTFS/root" ]; then ANANTA_WORKDIR="/root"; fi; ANANTA_LOGIN_SHELL=""; if [ -f "$ANANTA_ROOTFS/bin/sh" ]; then ANANTA_LOGIN_SHELL="/bin/sh"; elif [ -f "$ANANTA_ROOTFS/usr/bin/sh" ]; then ANANTA_LOGIN_SHELL="/usr/bin/sh"; elif [ -f "$ANANTA_ROOTFS/bin/bash" ]; then ANANTA_LOGIN_SHELL="/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/bash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/bash"; elif [ -f "$ANANTA_ROOTFS/usr/bin/dash" ]; then ANANTA_LOGIN_SHELL="/usr/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/dash" ]; then ANANTA_LOGIN_SHELL="/bin/dash"; elif [ -f "$ANANTA_ROOTFS/bin/ash" ]; then ANANTA_LOGIN_SHELL="/bin/ash"; fi; if [ -z "$ANANTA_LOGIN_SHELL" ]; then echo "keine Shell-Datei im rootfs gefunden ($ANANTA_ROOTFS)"; exit 1; fi; ANANTA_PROOT_TMP_BASE=""; for t in /data/user/0/com.ananta.mobile/cache "$ANANTA_PROOT_RUNTIME/tmp"; do if [ -d "$t" ] || mkdir -p "$t" 2>/dev/null; then ANANTA_PROOT_TMP_BASE="$t"; break; fi; done; [ -n "$ANANTA_PROOT_TMP_BASE" ] || { echo "kein beschreibbares temp-verzeichnis fuer proot gefunden"; exit 1; }; ANANTA_PROOT_TMP="$ANANTA_PROOT_TMP_BASE/ananta-proot"; mkdir -p "$ANANTA_PROOT_TMP" 2>/dev/null || true; chmod 700 "$ANANTA_PROOT_TMP" 2>/dev/null || true; unset PYTHONPATH PYTHONHOME PYTHONDONTWRITEBYTECODE PYTHONSTARTUP; export PROOT_FORCE_KOMPAT=1; export PROOT_TMP_DIR="$ANANTA_PROOT_TMP"; export TMPDIR="$ANANTA_PROOT_TMP"; export HOME=/root; export TERM=\${TERM:-xterm-256color}; export PATH="$ANANTA_ENV_PATH"; export LD_LIBRARY_PATH="$ANANTA_LIB_DIR:\${LD_LIBRARY_PATH:-}"; export GLIBC_TUNABLES=glibc.pthread.rseq=0; "$ANANTA_PROOT_DIRECT" -0 --link2symlink -r "$ANANTA_ROOTFS" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b "$ANANTA_PROOT_TMP:/tmp" -w "$ANANTA_WORKDIR" "$ANANTA_LOGIN_SHELL" -c '${workerStart}'; else echo "proot runtime oder rootfs fehlt. Bitte Runtime + Distro installieren."; exit 1; fi`,
    ].join(' && ');
  }

  buildWorkerStartCommand(): string {
    return this.workerStartSnippet();
  }

  private normalizeDistro(distro: string): string {
    const value = String(distro || '').trim().toLowerCase();
    return this.distroOptions.includes(value) ? value : 'ubuntu';
  }

  private apkProotResolverSnippet(): string {
    return [
      'ANANTA_APK_PATH="$(pm path com.ananta.mobile 2>/dev/null | sed -n \'1s/^package://p\')"',
      'ANANTA_LIB_DIR=""; ANANTA_PROOT_DIRECT=""',
      'if [ -n "$ANANTA_APK_PATH" ]; then ANANTA_LIB_DIR="$(dirname "$ANANTA_APK_PATH")/lib/arm64"; if [ -f "$ANANTA_LIB_DIR/libprootclassic.so" ]; then ANANTA_PROOT_DIRECT="$ANANTA_LIB_DIR/libprootclassic.so"; fi; fi',
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

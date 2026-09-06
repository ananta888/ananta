# Drittanbieter im optionalen lokalen Medienworker

`chromium-seccomp.json` stammt aus
[Microsoft Playwright v1.58.0](https://github.com/microsoft/playwright/blob/v1.58.0/utils/docker/seccomp_profile.json)
(Apache-2.0; vollständiger Lizenztext: `Apache-2.0.txt`).
Ananta ergänzt ausschließlich `chroot` in der unbedingten Namespace-Syscall-
Gruppe, damit Chromium mit `cap_drop: ALL` seinen inneren Namespace absichern
kann. Dadurch werden keine Host-Capabilities erteilt. Die Chromium-Sandbox
bleibt verpflichtend; das Profil ist kein `seccomp=unconfined`.

Copyright Microsoft Corporation. All rights reserved.
Upstream-Herkunft der Docker-Syscall-Basis und Lizenzhinweise im mitgelieferten
Apache-2.0-Lizenztext beachten.

Die Docker-Buildschritte laden weitere Drittanbieterpakete. Piper ist GPL-3.0,
ONNX Runtime MIT, Qwen2.5-1.5B-Instruct Apache-2.0; die gewählte Thorsten-
Modellkarte nennt CC0 für das Sprachdataset. FFmpeg-Buildoptionen und
NVIDIA-Laufzeitbedingungen bei Weitergabe separat berücksichtigen.
Es werden keine Modellgewichte oder Laufzeitbibliotheken in Git eingecheckt.

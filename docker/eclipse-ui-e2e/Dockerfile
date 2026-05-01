FROM eclipse-temurin:17-jdk-jammy

ARG ECLIPSE_TARBALL_URL=https://archive.eclipse.org/eclipse/downloads/drops4/R-4.30-202312010110/eclipse-platform-4.30-linux-gtk-x86_64.tar.gz

ENV DEBIAN_FRONTEND=noninteractive
ENV ECLIPSE_HOME=/opt/eclipse
ENV PATH="${ECLIPSE_HOME}:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus-x11 \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libxtst6 \
        python3 \
        tar \
        xvfb \
    && mkdir -p /opt \
    && curl -fsSL "${ECLIPSE_TARBALL_URL}" -o /tmp/eclipse.tar.gz \
    && tar -xzf /tmp/eclipse.tar.gz -C /opt \
    && rm -rf /var/lib/apt/lists/* /tmp/eclipse.tar.gz \
    && mkdir -p /workspace /tmp/ananta-eclipse-workspace

WORKDIR /workspace

ENTRYPOINT ["python3", "scripts/eclipse_ui_golden_path_runner.py"]

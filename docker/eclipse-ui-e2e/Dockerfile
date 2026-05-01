FROM eclipse-temurin:21-jdk-jammy

ARG ECLIPSE_TARBALL_URL=https://www.eclipse.org/downloads/download.php?file=/technology/epp/downloads/release/2026-03/R/eclipse-jee-2026-03-R-linux-gtk-x86_64.tar.gz&r=1

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
    && curl -fsSL https://repo1.maven.org/maven2/javax/persistence/javax.persistence-api/2.2/javax.persistence-api-2.2.jar -o /tmp/javax.persistence-api.jar \
    && mkdir -p /tmp/javax-persistence-bundle /opt/eclipse/dropins \
    && cd /tmp/javax-persistence-bundle \
    && jar xf /tmp/javax.persistence-api.jar \
    && printf '%s\n' \
        'Manifest-Version: 1.0' \
        'Bundle-ManifestVersion: 2' \
        'Bundle-SymbolicName: javax.persistence' \
        'Bundle-Name: Javax Persistence API' \
        'Bundle-Version: 2.2.0' \
        'Export-Package: javax.persistence;version="2.2.0",javax.persistence.criteria;version="2.2.0",javax.persistence.metamodel;version="2.2.0",javax.persistence.spi;version="2.2.0"' \
        > /tmp/javax-persistence-bundle/MANIFEST.MF \
    && jar cfm /opt/eclipse/dropins/javax.persistence_2.2.0.jar /tmp/javax-persistence-bundle/MANIFEST.MF -C /tmp/javax-persistence-bundle . \
    && rm -rf /var/lib/apt/lists/* /tmp/eclipse.tar.gz \
        /tmp/javax.persistence-api.jar /tmp/javax-persistence-bundle \
    && mkdir -p /workspace /tmp/ananta-eclipse-workspace

WORKDIR /workspace

ENTRYPOINT ["python3", "scripts/eclipse_ui_golden_path_runner.py"]

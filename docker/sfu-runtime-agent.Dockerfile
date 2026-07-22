FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends tpm2-tools \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 65532 runtime-agent \
    && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin runtime-agent

WORKDIR /opt/ananta-sfu-runtime
COPY sfu-runtime-agent/pyproject.toml ./pyproject.toml
COPY sfu-runtime-agent/src ./src
RUN python -m pip install --no-compile .

USER 65532:65532
EXPOSE 8443
ENTRYPOINT ["ananta-sfu-control-server"]

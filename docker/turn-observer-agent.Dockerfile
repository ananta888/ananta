# Supply an immutable value such as python:3.13.5-slim@sha256:<vendor digest>.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 65532 observer \
    && useradd --uid 65532 --gid 65532 --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin observer

WORKDIR /opt/ananta-turn-observer
COPY turn-observer-agent/pyproject.toml ./pyproject.toml
COPY turn-observer-agent/src ./src
RUN python -m pip install --no-deps . \
    && python -m pip install "cryptography==44.0.3"

USER 65532:65532
ENTRYPOINT ["ananta-turn-observer"]


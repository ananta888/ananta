FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/ananta

RUN groupadd --system --gid 65532 hrm-runner \
    && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin hrm-runner \
    && install -d -o 65532 -g 65532 -m 0750 /run/ananta-hrm /workspace

WORKDIR /opt/ananta
COPY docker/hrm-experiment-runner.requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-compile --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY agent/__init__.py ./agent/__init__.py
COPY agent/services/__init__.py ./agent/services/__init__.py
COPY agent/services/hrm_experiments ./agent/services/hrm_experiments
COPY worker/__init__.py ./worker/__init__.py
COPY worker/hrm_experiments ./worker/hrm_experiments
COPY schemas/hrm-experiments ./schemas/hrm-experiments

USER 65532:65532
ENTRYPOINT ["python", "-m", "worker.hrm_experiments.runner_server"]

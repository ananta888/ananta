FROM python:3.12-bookworm@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0
WORKDIR /app
ARG RUNTIME_LOCK=worker/decision/jeff-requirements.lock
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
COPY ${RUNTIME_LOCK} /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --extra-index-url ${TORCH_INDEX} -r /tmp/requirements.lock
RUN git clone https://github.com/logan-markewich/jeff.git /opt/jeff \
    && git -C /opt/jeff checkout --detach 34b32f99a727c47b679adde33f4702a001e02979 \
    && test "$(git -C /opt/jeff rev-parse HEAD)" = 34b32f99a727c47b679adde33f4702a001e02979 \
    && rm -rf /opt/jeff/.git \
    && useradd --uid 10001 --create-home jeff
COPY worker/decision /app/worker/decision
COPY ananta_contracts /app/ananta_contracts
ENV PYTHONPATH=/app:/opt/jeff/src \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
    JEFF_DEVICE=cpu JEFF_ISOLATE=all JEFF_REQUIRE_ASSIGNMENT=1 OMP_NUM_THREADS=2
USER 10001:10001
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["python", "-m", "worker.decision.jeff_runtime"]

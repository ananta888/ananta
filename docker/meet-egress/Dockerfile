FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY ananta_contracts/__init__.py ananta_contracts/meet_egress.py /app/ananta_contracts/
COPY worker/meet_egress /app/worker/meet_egress
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
HEALTHCHECK --interval=5s --timeout=2s --start-period=2s --retries=2 CMD ["python", "-m", "worker.meet_egress.health"]
CMD ["python", "-m", "worker.meet_egress.service"]

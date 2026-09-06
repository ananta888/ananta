FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
WORKDIR /app
RUN pip install --no-cache-dir Pillow==11.3.0
COPY ananta_contracts /app/ananta_contracts
COPY worker/meet_media /app/worker/meet_media
COPY voice_runtime /app/voice_runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "worker.meet_media.persona_server"]

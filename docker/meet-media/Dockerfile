FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY docker/meet-media/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y onnxruntime \
    && pip install --no-cache-dir --force-reinstall --no-deps onnxruntime-gpu==1.22.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/meet-browser
RUN pip install --no-cache-dir playwright==1.58.0 \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/meet-browser
COPY worker/meet_media /app/worker/meet_media
COPY voice_runtime /app/voice_runtime
ENV PYTHONUNBUFFERED=1 NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
CMD ["python", "-m", "worker.meet_media.server"]

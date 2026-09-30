FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ARG ANANTA_REVISION=unknown
LABEL org.opencontainers.image.revision="${ANANTA_REVISION}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py oidc_auth.py pair_security.py peer_identity.py service.py app.py ./
COPY rendezvous_runtime.py rendezvous_records.py rendezvous_membership.py rendezvous_schema.py rendezvous_session_admission.py rendezvous_session_catalog.py rendezvous_membership_operations.py rendezvous_key_exchange.py rendezvous_signal_relay.py rendezvous_turn_credentials.py ./

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

EXPOSE 5000

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]

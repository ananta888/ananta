# Test-only dependency image must be supplied by immutable digest/image ID.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /fixtureapp
COPY --chown=1000:1000 . /fixtureapp
ENV PYTHONPATH=/fixtureapp PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ROLE=hub DATABASE_URL=sqlite:////state/hub.sqlite DATA_DIR=/state/data \
    FLASK_DEBUG=0 MEET_HUB_RESTART_GATE=1
USER 1000:1000
HEALTHCHECK NONE
ENTRYPOINT ["timeout", "300"]
CMD ["python", "-m", "tests.meet_restart_hub_runtime"]

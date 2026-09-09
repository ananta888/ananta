# Supply a repository digest, or a private local tag whose image ID is verified
# before/after building. BuildKit does not accept bare sha256 image IDs in FROM.
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

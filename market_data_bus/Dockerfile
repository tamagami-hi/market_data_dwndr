FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --gid "${APP_GID}" mdbus \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin mdbus

WORKDIR /app

RUN pip install --no-cache-dir "pyzmq==27.2.0"

COPY mdbus ./mdbus

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MDBUS_INGEST_ENDPOINT=tcp://0.0.0.0:7301 \
    MDBUS_PUBLISH_ENDPOINT=tcp://0.0.0.0:7302 \
    MDBUS_HEALTH_ENDPOINT=tcp://0.0.0.0:7303

USER ${APP_UID}:${APP_GID}

CMD ["python", "-m", "mdbus.service"]

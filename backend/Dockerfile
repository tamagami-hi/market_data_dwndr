FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
ENV UV_PROJECT_ENVIRONMENT="${VIRTUAL_ENV}"

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN pip install --no-cache-dir uv==0.10.11 \
    && uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/tmp

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

USER ${APP_UID}:${APP_GID}
CMD ["python", "-m", "app.server"]

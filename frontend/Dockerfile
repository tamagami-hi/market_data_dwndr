FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS builder

ARG NEXT_PUBLIC_BACKEND_URL
ARG NEXT_PUBLIC_APP_NAME=TickVault
ENV NEXT_PUBLIC_BACKEND_URL="${NEXT_PUBLIC_BACKEND_URL}"
ENV NEXT_PUBLIC_APP_NAME="${NEXT_PUBLIC_APP_NAME}"
ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN test -n "${NEXT_PUBLIC_BACKEND_URL}" \
    && npm run build

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV HOME=/tmp

WORKDIR /app
COPY --from=builder --chown=${APP_UID}:${APP_GID} /app/.next/standalone ./
COPY --from=builder --chown=${APP_UID}:${APP_GID} /app/.next/static ./.next/static
RUN install -d -o "${APP_UID}" -g "${APP_GID}" .next/cache

USER ${APP_UID}:${APP_GID}
CMD ["node", "server.js"]

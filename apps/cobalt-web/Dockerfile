# syntax=docker/dockerfile:1

ARG VERSION
FROM ghcr.io/imputnet/cobalt:${VERSION} AS upstream

FROM docker.io/library/node:24-alpine AS build
ARG WEB_DEFAULT_API="https://cobalt-api.nikola.wtf/"

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

WORKDIR /src

RUN apk add --no-cache git

# upstream has no git tags; the api image embeds .git at the exact
# commit the release was built from, so restore the source tree from it
COPY --from=upstream /app/.git /src/.git
RUN git config --global --add safe.directory /src \
    && git reset --hard

RUN corepack enable

RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter "@imput/cobalt-web..."

RUN WEB_DEFAULT_API="${WEB_DEFAULT_API}" pnpm --dir web build

FROM docker.io/nginxinc/nginx-unprivileged:1.31-alpine

COPY --from=build /src/web/build /usr/share/nginx/html
COPY ./defaults/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

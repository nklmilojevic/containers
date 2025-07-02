FROM docker.io/library/python:3.13-alpine3.22
ARG TARGETPLATFORM
ARG VERSION
ARG CHANNEL

ENV QBT_CONFIRM_LEGAL_NOTICE=1 \
    HOME="/config" \
    XDG_CONFIG_HOME="/config" \
    XDG_DATA_HOME="/config"

USER root
WORKDIR /app

RUN \
    apk add --no-cache \
    7zip \
    bash \
    ca-certificates \
    catatonit \
    coreutils \
    curl \
    jq \
    nano \
    tzdata \
    && \
    mkdir -p /app \
    && LIBTORRENT=$(curl -sL "https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/dependency-version.json" | jq -r '.libtorrent_2_0') \
    && curl -fsSL -o /app/qbittorrent-nox "https://github.com/userdocs/qbittorrent-nox-static/releases/download/release-${VERSION}_v${LIBTORRENT}/${TARGETARCH}-qbittorrent-nox" \
    && chown -R root:root /app/qbittorrent-nox \
    && chmod -R 755 /app/qbittorrent-nox \
    && rm -rf /tmp/*

COPY ./apps/qbittorrent/qBittorrent.conf /app/qBittorrent.conf
COPY ./apps/qbittorrent/entrypoint.sh /entrypoint.sh

COPY --from=ghcr.io/linuxserver/unrar:latest /usr/bin/unrar-alpine /usr/bin/unrar

USER nobody:nogroup
WORKDIR /config
VOLUME ["/config"]

ENTRYPOINT ["/usr/bin/catatonit", "--", "/entrypoint.sh"]
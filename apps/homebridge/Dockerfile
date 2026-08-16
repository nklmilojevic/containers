# syntax=docker/dockerfile:1

# Rootless Homebridge with homebridge-unifi-protect baked in (no runtime npm
# installs, plugins are pinned here and updated by Renovate). Two ffmpeg
# builds ship in the image: the plugin's bundled ffmpeg-for-homebridge static
# build (full libfdk-aac, AAC-ELD audio for HomeKit — the safe default) and
# jellyfin-ffmpeg (Intel QSV/VAAPI with a vendored libva/iHD driver stack,
# fdk-aac-stripped) selectable via the plugin's videoProcessor option for
# hardware-accelerated transcoding at /usr/lib/jellyfin-ffmpeg/ffmpeg.
FROM docker.io/library/ubuntu:24.04
ARG TARGETARCH
ARG VERSION

# renovate: datasource=npm depName=homebridge-config-ui-x
ARG UI_VERSION=5.28.0
# renovate: datasource=npm depName=homebridge-unifi-protect
ARG PROTECT_VERSION=8.1.0
# renovate: datasource=github-releases depName=jellyfin/jellyfin-ffmpeg
ARG JELLYFIN_FFMPEG_VERSION=v7.1.4-3

ENV DEBIAN_FRONTEND="noninteractive" \
    HOME="/config"

USER root
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
        ca-certificates \
        catatonit \
        curl \
        gnupg \
        tzdata \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL -o /tmp/jellyfin-ffmpeg.deb \
        "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/${JELLYFIN_FFMPEG_VERSION}/jellyfin-ffmpeg7_${JELLYFIN_FFMPEG_VERSION#v}-noble_${TARGETARCH}.deb" \
    && apt-get install -y --no-install-recommends /tmp/jellyfin-ffmpeg.deb \
    && npm install -g --omit=dev --foreground-scripts \
        "homebridge@${VERSION}" \
        "homebridge-config-ui-x@${UI_VERSION}" \
        "homebridge-unifi-protect@${PROTECT_VERSION}" \
    && FFMPEG="$(node -p "require('/usr/lib/node_modules/homebridge-unifi-protect/node_modules/ffmpeg-for-homebridge')")" \
    && "${FFMPEG}" -version \
    && /usr/lib/jellyfin-ffmpeg/ffmpeg -version \
    && npm cache clean --force \
    && mkdir -p /config \
    && chown -R nobody:nogroup /config \
    && apt-get purge -y --auto-remove gnupg \
    && apt-get clean \
    && rm -rf /root/.npm /tmp/* /var/lib/apt/lists/* /var/tmp/*

USER nobody:nogroup
WORKDIR /config
VOLUME ["/config"]
EXPOSE 8581

ENTRYPOINT ["/usr/bin/catatonit", "--", "hb-service", "run", "--user-storage-path", "/config", "--stdout"]

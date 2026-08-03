# syntax=docker/dockerfile:1

FROM docker.io/library/python:3.12-alpine
ARG VERSION
ARG TARGETARCH

# renovate: datasource=github-releases depName=AlexxIT/go2rtc
ARG GO2RTC_VERSION=v1.9.14

ENV PYTHONPATH=/opt/petkit-local \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /opt/petkit-local

# ffmpeg remuxes device camera clips; go2rtc republishes the camera stream as
# RTSP. Both degrade gracefully on camera-less devices.
RUN apk add --no-cache catatonit ffmpeg \
    && wget -qO- "https://github.com/alex-so-3/petkit-local/archive/refs/tags/${VERSION}.tar.gz" | tar xz -C /tmp \
    && pip install -r "/tmp/petkit-local-${VERSION#v}/addon/requirements.txt" \
    && cp -r "/tmp/petkit-local-${VERSION#v}/addon/petkit_local" ./petkit_local \
    && rm -rf "/tmp/petkit-local-${VERSION#v}" \
    && wget -qO /usr/local/bin/go2rtc "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${TARGETARCH}" \
    && chmod +x /usr/local/bin/go2rtc \
    && mkdir -p /data /media/petkit \
    && chown -R 1000:1000 /data /media/petkit

# Overlay: fix Ingenic (T5/T6/T7/D4H/D4SH) BLE provisioning, which upstream
# 1.6.0 still sends as bare JSON — those devices answer only to the framed
# FA FC FD 46 envelope written as one writeWithoutResponse. Drop this once the
# fix is upstream. Rebuilt from upstream's own app.js for ${VERSION}.
COPY ./defaults/app.js ./petkit_local/web/static/app.js

USER 1000
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080 8099 8443 9000

ENTRYPOINT ["/usr/bin/catatonit", "--", "python3", "-m", "petkit_local.main"]

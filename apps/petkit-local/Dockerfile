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
# Built from upstream alex-so-3/petkit-local at tag ${VERSION}. The fork-era
# D4H fixes (BLE provisioning, camera feeder IP, desiccant countdown, feed-clip
# upload chain, cameraMultiNew schedule objects, bucket-cert SAN, feed totals,
# feedSound, cacert dedup) all landed upstream by v2.1.0. The GitHub tag
# archive extracts to petkit-local-${VERSION#v}/.
RUN apk add --no-cache catatonit ffmpeg \
    && wget -qO- "https://github.com/alex-so-3/petkit-local/archive/refs/tags/${VERSION}.tar.gz" | tar xz -C /tmp \
    && pip install -r "/tmp/petkit-local-${VERSION#v}/addon/requirements.txt" \
    && cp -r "/tmp/petkit-local-${VERSION#v}/addon/petkit_local" ./petkit_local \
    && rm -rf "/tmp/petkit-local-${VERSION#v}" \
    && wget -qO /usr/local/bin/go2rtc "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${TARGETARCH}" \
    && chmod +x /usr/local/bin/go2rtc \
    && mkdir -p /data /media/petkit \
    && chown -R 1000:1000 /data /media/petkit

USER 1000
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080 8099 8443 9000

ENTRYPOINT ["/usr/bin/catatonit", "--", "python3", "-m", "petkit_local.main"]

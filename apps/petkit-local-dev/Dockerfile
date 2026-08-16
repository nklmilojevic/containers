# syntax=docker/dockerfile:1

# petkit-local-dev — vendored source build.
#
# Sibling of ../petkit-local/, which pulls the nklmilojevic/petkit-local fork
# from a GitHub tag archive. This one ships the same source COPY-vendored under
# ./addon/, so patches can be iterated inside this repo without cutting a fork
# tag per commit. Kept in step by hand with the fork's fix/http-k3-piggyback
# branch; when alex-so-3#21 lands upstream, delete this whole app and go back
# to the tag-fetching sibling pointed at plain upstream.

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

# Vendored sources — everything the runtime needs is under ./addon/. Tests and
# CHANGELOG stay in the tree for iteration but are copied into a scratch dir
# and discarded so the runtime image only carries petkit_local + deps.
COPY addon/requirements.txt /tmp/requirements.txt
COPY addon/petkit_local ./petkit_local

RUN apk add --no-cache catatonit ffmpeg \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && wget -qO /usr/local/bin/go2rtc "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_${TARGETARCH}" \
    && chmod +x /usr/local/bin/go2rtc \
    && mkdir -p /data /media/petkit \
    && chown -R 1000:1000 /data /media/petkit

USER 1000
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080 8099 8443 9000

ENTRYPOINT ["/usr/bin/catatonit", "--", "python3", "-m", "petkit_local.main"]

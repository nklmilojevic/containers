# syntax=docker/dockerfile:1

FROM docker.io/library/python:3.13-alpine3.22
ARG VERSION

ENV \
    HOME="/config" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV HEADPHONES__HOST="0.0.0.0" \
    HEADPHONES__PORT="8181"

USER root
WORKDIR /app

RUN \
    apk add --no-cache \
        bash \
        ca-certificates \
        catatonit \
        curl \
        flac \
        lame \
        tzdata \
    && mkdir -p /app \
    && curl -fsSL "https://github.com/rembo10/headphones/archive/refs/tags/${VERSION}.tar.gz" \
        | tar xzf - -C /app --strip-components=1 \
    # musicbrainz.org no longer answers plain http; use https (upstream hardcodes port 80)
    && sed -i \
        -e '/mbhost = "musicbrainz.org"/{n;s|mbport = 80|mbport = 443|;}' \
        -e 's|musicbrainzngs.set_hostname(mbhost + ":" + str(mbport))|musicbrainzngs.set_hostname(mbhost + ":" + str(mbport), mbport == 443)|' \
        /app/headphones/mb.py \
    && grep -q 'mbport == 443' /app/headphones/mb.py \
    && chown -R root:root /app && chmod -R 755 /app \
    && rm -rf /root/.cache /tmp/*

COPY . /

USER nobody:nogroup
WORKDIR /config
VOLUME ["/config"]

ENTRYPOINT ["/usr/bin/catatonit", "--", "/entrypoint.sh"]

# syntax=docker/dockerfile:1

ARG VERSION
FROM docker.io/library/alpine:${VERSION}

RUN apk add --no-cache \
        bash \
        catatonit \
        openssh-server \
        openssh-client \
        openssh-sftp-server \
    && adduser -D -u 1000 -h /config -s /bin/bash me \
    && mkdir -p /config \
    && chown -R me:me /config \
    && rm -f /etc/ssh/ssh_host_* \
    # allow an arbitrary (non-1000) uid to resolve to a user, OpenShift-style
    && chmod g=u /etc/passwd

COPY --chmod=0755 entrypoint.sh /entrypoint.sh

ENV PORT=2222 \
    USER_NAME=me \
    HOME=/config

USER 1000
WORKDIR /config
VOLUME ["/config"]
EXPOSE 2222
ENTRYPOINT ["/usr/bin/catatonit", "--", "/entrypoint.sh"]

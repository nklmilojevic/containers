# syntax=docker/dockerfile:1

# Built from nklmilojevic/hms-cpap (fork of hms-homelab/hms-cpap) at tag
# ${VERSION}, which carries the multi-EVE sidecar fix (upstream #22) and pins
# nklmilojevic/hms-cpapdash-parser v2026.1.10-nkl.1 (multi-BRP timestamp
# collision fixed upstream in v2026.1.10 but never shipped; multi-EVE
# parseSession added in the fork). Revert SOURCE/VERSION to hms-homelab once
# the fixes land upstream. The GitHub tag archive extracts to
# hms-cpap-${VERSION#v}/.

# =============================================================================
# Stage 1: Angular UI Builder
# =============================================================================
FROM docker.io/library/node:24-slim AS ui-builder
ARG VERSION

ADD https://github.com/nklmilojevic/hms-cpap/archive/refs/tags/${VERSION}.tar.gz /tmp/src.tar.gz
RUN mkdir -p /src && tar xzf /tmp/src.tar.gz -C /src --strip-components=1

WORKDIR /ui
RUN cp /src/frontend/package.json /src/frontend/package-lock.json ./ \
    && npm ci --no-audit --no-fund \
    && cp -r /src/frontend/. ./ \
    && npx ng build --configuration production

# =============================================================================
# Stage 2: C++ Builder
# =============================================================================
FROM docker.io/library/debian:trixie-slim AS builder
ARG VERSION

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ca-certificates \
    git \
    curl \
    libcurl4-openssl-dev \
    libpq-dev \
    libpqxx-dev \
    libssl-dev \
    libjsoncpp-dev \
    libpaho-mqtt-dev \
    libpaho-mqttpp-dev \
    nlohmann-json3-dev \
    libspdlog-dev \
    libsqlite3-dev \
    libdrogon-dev \
    uuid-dev libmariadb-dev libhiredis-dev libbrotli-dev \
    libyaml-cpp-dev zlib1g-dev \
    libhpdf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ADD https://github.com/nklmilojevic/hms-cpap/archive/refs/tags/${VERSION}.tar.gz /tmp/src.tar.gz
RUN tar xzf /tmp/src.tar.gz -C /build --strip-components=1 && rm /tmp/src.tar.gz

# cpapdash-parser is pulled by CMake FetchContent from the fork tag pinned in
# CMakeLists.txt (no local sibling checkout exists in this context).
RUN mkdir build && cd build && \
    cmake -DBUILD_TESTS=OFF -DBUILD_WITH_WEB=ON -DBUILD_WITH_MYSQL=ON .. && \
    make -j$(nproc) hms_cpap && \
    strip hms_cpap

# =============================================================================
# Stage 3: Runtime
# =============================================================================
FROM docker.io/library/debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tzdata \
    libcurl4t64 \
    libpq5 \
    libpqxx-7.10 \
    libssl3 \
    libjsoncpp26 \
    libpaho-mqtt1.3 \
    libpaho-mqttpp3-1 \
    libspdlog1.15 \
    libfmt10 \
    libsqlite3-0 \
    libmariadb3 \
    libdrogon1t64 \
    libtrantor1 \
    libhpdf-2.3.0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -u 1000 -m -s /bin/bash cpap

COPY --from=builder /build/build/hms_cpap /usr/local/bin/hms_cpap
COPY --from=ui-builder /ui/dist/frontend/browser/ /home/cpap/static/browser/

# config.json (and the SQLite DB, when used) live in /config so they survive a
# container recreate even when the operator mounts nothing (upstream issue #16).
RUN mkdir -p /data/cpap_archive /tmp/hms-cpap /config && \
    chown -R cpap:cpap /data /tmp/hms-cpap /config /home/cpap/static

ENV HMS_CPAP_DATA_DIR=/config
VOLUME ["/config"]

USER cpap
WORKDIR /home/cpap

EXPOSE 8893

ENTRYPOINT ["/usr/local/bin/hms_cpap"]

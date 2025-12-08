FROM docker.io/library/python:3.14.2-slim-bookworm

ARG VERSION

ENV \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  PIP_ROOT_USER_ACTION=ignore \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PIP_BREAK_SYSTEM_PACKAGES=1 \
  UV_SYSTEM_PYTHON=true \
  CRYPTOGRAPHY_DONT_BUILD_RUST=1 \
  HOME="/config" \
  PLATFORMIO_CORE_DIR=/cache/pio \
  ESPHOME_BUILD_PATH=/cache/build \
  ESPHOME_DATA_DIR=/cache/data \
  DEBIAN_FRONTEND=noninteractive

USER root
WORKDIR /app

RUN \
  apt-get update \
  && apt-get install -y --no-install-recommends \
  build-essential \
  catatonit \
  git \
  && pip install uv \
  && uv pip install "setuptools" "esphome[displays]==${VERSION}" \
  && mkdir /cache \
  && chown nobody:nogroup /cache \
  && apt-get clean \
  && rm -rf \
  /var/lib/apt/lists/* \
  /tmp/* \
  /wheels

COPY ./apps/esphome/entrypoint.sh /entrypoint.sh
USER nobody:nogroup
WORKDIR /config
VOLUME ["/config", "/cache"]

ENTRYPOINT ["/usr/bin/catatonit", "--", "/entrypoint.sh"]
CMD ["dashboard", "/config"]
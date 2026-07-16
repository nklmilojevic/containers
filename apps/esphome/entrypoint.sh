#!/usr/bin/env bash

PLATFORMIO_CORE_DIR=${PLATFORMIO_CORE_DIR:-/cache/pio}
ESPHOME_BUILD_PATH=${ESPHOME_BUILD_PATH:-/cache/build}
ESPHOME_DATA_DIR=${ESPHOME_DATA_DIR:-/cache/data}

# Make sure cache folders exist
mkdir -p "${PLATFORMIO_CORE_DIR}"
mkdir -p "${ESPHOME_BUILD_PATH}"
mkdir -p "${ESPHOME_DATA_DIR}"

# Prune PIO files
pio system prune --force

# The built-in dashboard was removed from ESPHome in 2026.7.0; route the
# dashboard subcommand to ESPHome Device Builder, pass everything else
# straight through to the esphome CLI
if [[ "$1" == "dashboard" ]]; then
    shift
    exec /usr/local/bin/esphome-device-builder "$@"
fi

# Launch ESPHome
exec /usr/local/bin/esphome "$@"

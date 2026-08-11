#!/usr/bin/env bash

exec \
    /usr/local/bin/python \
        /app/Headphones.py \
        --datadir /config \
        --nolaunch \
        --host "${HEADPHONES__HOST}" \
        --port "${HEADPHONES__PORT}" \
        "$@"

#!/bin/bash
# Build static dropbear binaries for PetKit devices.
#
# Produces two multi-call binaries (dropbear + dropbearkey):
#   dropbear-mipsel  — Ingenic MIPS32r2 LE (T5, T6, T7, D4H, D4SH)
#   dropbear-armv7   — ARMv7-A hard-float   (W7H)
#
# Requires Docker. Run from the directory you want the output in.
# Both builds use musl for clean static linking (glibc breaks getpwnam).
#
# Source modifications from stock dropbear 2024.86:
#   1. authorized_keys path hardcoded to /tmp/.ssh/authorized_keys
#      (root home is / on squashfs, read-only)
#   2. Buffer size for that path bumped from len+22 to len+32
#   3. checkfileperm() returns SUCCESS immediately
#      (squashfs ownership + tmpfs 1777 fail the checks)
#   4. Host key paths default to /tmp/dbkey_*
#   5. Password auth disabled (device /etc/shadow uses DES crypt)
#
# See addon/petkit_local/web/static/bin/README.md for full details.
set -euo pipefail

OUTDIR="$(cd "$(dirname "$0")" && pwd)"
DROPBEAR_VERSION="2024.86"
DROPBEAR_URL="https://matt.ucc.asn.au/dropbear/releases/dropbear-${DROPBEAR_VERSION}.tar.bz2"

build_one() {
    local TRIPLE="$1" SUFFIX="$2" ARCH_CFLAGS="$3"
    local BINNAME="dropbear-${SUFFIX}"
    local IMAGE="dropbear-${SUFFIX}-builder"

    echo "=== Building ${BINNAME} (${TRIPLE}) ==="

    docker build --no-cache -t "$IMAGE" -f - . <<DOCKERFILE
FROM debian:bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    make wget ca-certificates bzip2 xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && wget -q https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-amd64_linux.tar.xz \
    && tar xf upx-4.2.4-amd64_linux.tar.xz \
    && mv upx-4.2.4-amd64_linux/upx /usr/local/bin/ \
    && rm -rf upx-4.2.4-amd64_linux*

RUN wget -q https://musl.cc/${TRIPLE}-cross.tgz \
    && tar xf ${TRIPLE}-cross.tgz -C /opt \
    && rm ${TRIPLE}-cross.tgz
ENV PATH="/opt/${TRIPLE}-cross/bin:\$PATH"

WORKDIR /build
RUN wget -q ${DROPBEAR_URL} \
    && tar xf dropbear-${DROPBEAR_VERSION}.tar.bz2 \
    && rm dropbear-${DROPBEAR_VERSION}.tar.bz2

WORKDIR /build/dropbear-${DROPBEAR_VERSION}

RUN AUTHFILE=\$(find . -name 'svr-authpubkey.c') \
    && sed -i 's|"%s/.ssh/authorized_keys"|"/tmp/.ssh/authorized_keys"|' "\$AUTHFILE" \
    && sed -i 's|len + 22|len + 32|g' "\$AUTHFILE"

RUN AUTHFILE=\$(find . -name 'svr-authpubkey.c') \
    && sed -i 's/static int checkfileperm(char \\* filename) {/static int checkfileperm(char * filename) { return DROPBEAR_SUCCESS;/' "\$AUTHFILE"

RUN printf '%s\\n' \
    '#define DSS_PRIV_FILENAME "/tmp/dbkey_dss"' \
    '#define RSA_PRIV_FILENAME "/tmp/dbkey_rsa"' \
    '#define ECDSA_PRIV_FILENAME "/tmp/dbkey_ecdsa"' \
    '#define ED25519_PRIV_FILENAME "/tmp/dbkey_ed25519"' \
    '#define DROPBEAR_SVR_PASSWORD_AUTH 0' \
    > localoptions.h

RUN ./configure \
    --host=${TRIPLE} \
    --disable-zlib \
    --disable-wtmp \
    --disable-lastlog \
    --disable-syslog \
    CC=${TRIPLE}-gcc \
    LDFLAGS="-static -Wl,--gc-sections" \
    CFLAGS="-Os ${ARCH_CFLAGS} -ffunction-sections -fdata-sections"

RUN make -j\$(nproc) PROGRAMS="dropbear dropbearkey" MULTI=1 \
    && ${TRIPLE}-strip dropbearmulti \
    && upx --best --lzma dropbearmulti \
    && ls -lh dropbearmulti
DOCKERFILE

    local CONTAINER
    CONTAINER=$(docker create "$IMAGE")
    docker cp "$CONTAINER:/build/dropbear-${DROPBEAR_VERSION}/dropbearmulti" "$OUTDIR/${BINNAME}"
    docker rm "$CONTAINER" > /dev/null

    local SIZE SHA
    SIZE=$(stat -c%s "$OUTDIR/${BINNAME}" 2>/dev/null || stat -f%z "$OUTDIR/${BINNAME}")
    SHA=$(sha256sum "$OUTDIR/${BINNAME}" | cut -d' ' -f1)
    echo "${BINNAME}: ${SIZE} bytes, SHA-256 ${SHA}"
    echo
}

build_one "mipsel-linux-musl"        "mipsel" "-mips32r2"
build_one "armv7l-linux-musleabihf"  "armv7"  "-march=armv7-a -mfloat-abi=hard -mfpu=neon"

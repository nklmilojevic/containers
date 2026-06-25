#!/usr/bin/env bash
set -euo pipefail

ssh_dir="${HOME}/.ssh"
mkdir -p "${ssh_dir}"
chmod 700 "${ssh_dir}"

# Authorized keys come from the environment (public keys are not secret).
# Supports a single key in PUBLIC_KEY or a file path in PUBLIC_KEY_FILE.
if [[ -n "${PUBLIC_KEY:-}" ]]; then
    printf '%s\n' "${PUBLIC_KEY}" >"${ssh_dir}/authorized_keys"
elif [[ -n "${PUBLIC_KEY_FILE:-}" && -f "${PUBLIC_KEY_FILE}" ]]; then
    cat "${PUBLIC_KEY_FILE}" >"${ssh_dir}/authorized_keys"
fi
[[ -f "${ssh_dir}/authorized_keys" ]] && chmod 600 "${ssh_dir}/authorized_keys"

# Persist the host key on the mounted volume so it survives restarts
# (no host-key-changed warnings on the client).
host_key="${ssh_dir}/ssh_host_ed25519_key"
if [[ ! -f "${host_key}" ]]; then
    ssh-keygen -t ed25519 -f "${host_key}" -N "" -q
fi

# Ensure the running uid resolves to a user (when not the baked-in uid 1000).
if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
    printf '%s:x:%s:0:%s:%s:/bin/bash\n' \
        "${USER_NAME}" "$(id -u)" "${USER_NAME}" "${HOME}" >>/etc/passwd 2>/dev/null || true
fi

# sshd runs entirely rootless: it only ever serves the user it runs as
# (no setuid needed), listens on an unprivileged port, and keeps all state
# under the user-writable home. StrictModes is relaxed because the volume is
# owned via fsGroup rather than strictly by the user.
exec /usr/sbin/sshd -D -e \
    -p "${PORT:-2222}" \
    -h "${host_key}" \
    -o "PidFile=${ssh_dir}/sshd.pid" \
    -o "PasswordAuthentication=no" \
    -o "PubkeyAuthentication=yes" \
    -o "AuthorizedKeysFile=${ssh_dir}/authorized_keys" \
    -o "StrictModes=no" \
    -o "Subsystem=sftp /usr/lib/ssh/sftp-server"

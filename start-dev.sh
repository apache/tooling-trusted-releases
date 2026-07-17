#!/bin/bash
set -eu

# shellcheck source=/dev/null
source .venv/bin/activate

mkdir -p /opt/atr/state 2>/dev/null || true
if [ ! -w /opt/atr/state ]
then
  echo "ERROR: /opt/atr/state is missing or not writable" >&2
  echo "Process user $(id -u):$(id -g); state directory $(stat -c 'uid %u gid %g mode %a' /opt/atr/state 2>/dev/null || echo missing)" >&2
  echo "Run the container as a user that can write to the state directory, or change its ownership to match" >&2
  exit 1
fi

if [ ! -f state/hypercorn/secrets/cert.pem ] || [ ! -f state/hypercorn/secrets/key.pem ]
then
  # The generate-certificates script creates the necessary directories
  python3 scripts/generate-certificates
fi

# Ensure that the permissions of secret files are correct
STATE_DIR=state scripts/check-perms

mkdir -p /opt/atr/state/hypercorn/logs
echo "Starting hypercorn on ${BIND}" >> /opt/atr/state/hypercorn/logs/hypercorn.log
exec hypercorn --worker-class uvloop --reload --bind "${BIND}" \
  --keyfile hypercorn/secrets/key.pem \
  --certfile hypercorn/secrets/cert.pem \
  atr.server:app | tee /opt/atr/state/hypercorn/logs/hypercorn.log 2>&1

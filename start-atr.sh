#!/bin/bash
set -eu

# shellcheck source=/dev/null
source .venv/bin/activate

test -d /opt/atr/state || mkdir -p /opt/atr/state

if [ ! -f state/hypercorn/secrets/cert.pem ] || [ ! -f state/hypercorn/secrets/key.pem ]
then
  # The generate-certificates script creates the necessary directories
  python3 scripts/generate-certificates
fi

# Ensure that the permissions of secret files are correct
STATE_DIR=state scripts/check-perms

mkdir -p /opt/atr/state/hypercorn/logs
echo "Starting hypercorn on ${BIND}" >> /opt/atr/state/hypercorn/logs/hypercorn.log
exec hypercorn --worker-class uvloop --bind "${BIND}" \
  --keyfile hypercorn/secrets/key.pem \
  --certfile hypercorn/secrets/cert.pem \
  atr.server:app >> /opt/atr/state/hypercorn/logs/hypercorn.log 2>&1

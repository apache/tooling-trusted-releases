#!/bin/sh
set -eu

cd "$(dirname "$0")"

echo "Building and running ATR e2e tests against the built image..."
if docker compose up atr e2e --build --abort-on-container-exit --exit-code-from e2e
then
  exit_code=0
else
  exit_code=$?
  echo "ERROR: e2e tests failed with exit code ${exit_code}"
  echo "Server logs:"
  docker compose cp atr:/opt/atr/state/hypercorn/logs/hypercorn.log - 2>/dev/null \
    | tar -xO 2>/dev/null \
    | tail -n 200 || true
fi

# Clean up
docker compose down -v

exit "${exit_code}"

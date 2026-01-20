#!/bin/sh
set -eu

cd "$(dirname "$0")"

echo "Running ATR e2e tests..."

if ! docker compose ps atr-dev --status running -q 2>/dev/null | grep -q .
then
  echo "Starting ATR dev container..."
  docker compose up atr-dev -d --build --wait

  if ! docker compose ps atr-dev --status running -q 2>/dev/null | grep -q .
  then
    echo "ERROR: the atr-dev container failed to start or crashed during startup"
    echo "Container logs:"
    docker compose logs atr-dev --tail 100
    exit 1
  fi
fi

docker compose build e2e-dev
if ! docker compose run --rm e2e-dev pytest e2e/ -v
then
  exit_code=$?
  echo "ERROR: e2e tests failed with exit code ${exit_code}"
  echo "View the container logs with 'docker compose logs atr-dev --tail 100'"
  exit "${exit_code}"
fi

echo "Use 'docker compose down atr-dev' to stop the dev container"

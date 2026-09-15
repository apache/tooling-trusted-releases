#!/bin/sh
set -eux

if [ $# -ne 1 ]
then
  echo "usage: bump.sh VERSION" >&2
  echo "example: bump.sh 5.4.0" >&2
  exit 1
fi

VERSION="$1"
SOURCE="${SOURCE:-/opt/bootstrap/source}"

cd "$SOURCE"

if ! command -v npm >/dev/null 2>&1
then
  echo "error: npm not found" >&2
  exit 1
fi

# Alternative: use Bootstrap publish date as cutoff
# PUBLISH_DATE=$(npm view "bootstrap@$VERSION" time)
# if [ -z "$PUBLISH_DATE" ]
# then
#   echo "error: could not determine publish date for bootstrap@$VERSION" >&2
#   exit 1
# fi
# npm install "bootstrap@$VERSION" --before="$PUBLISH_DATE"

SECONDS_AGO=$((14 * 24 * 60 * 60))
CUTOFF=$(date -u -d "@$(($(date +%s) - SECONDS_AGO))" +%Y-%m-%dT%H:%M:%SZ)

echo "Using 14-day cooldown: --before=$CUTOFF"

rm -rf node_modules package-lock.json

npm install "bootstrap@$VERSION" --before="$CUTOFF"

echo "Checking for known vulnerabilities..."
npm audit

echo "Verifying registry signatures..."
npm audit signatures

# Record the cutoff so scripts/check_npm_dependencies_updated.py can flag a stale bump, the way
# uv.lock's exclude-newer feeds the Python check - package-lock.json carries no timestamp of its own
printf '%s\n' "$CUTOFF" > .npm-exclude-newer

echo "Bootstrap updated to version $VERSION"
echo "Please commit the updated package.json, package-lock.json and .npm-exclude-newer"

#!/bin/sh
set -eux

SOURCE="${SOURCE:-/opt/bootstrap/source}"
OUTPUT="${OUTPUT:-/run/bootstrap-output}"

cd "$SOURCE"

if ! command -v npm >/dev/null 2>&1
then
  echo "error: npm not found" >&2
  exit 1
fi

if ! command -v sass >/dev/null 2>&1
then
  echo "error: sass not found" >&2
  exit 1
fi

if [ ! -f package.json ]
then
  echo "error: package.json not found" >&2
  exit 1
fi

if [ ! -f package-lock.json ]
then
  echo "error: package-lock.json not found" >&2
  exit 1
fi

if [ ! -d "$OUTPUT" ]
then
  echo "error: output directory not found at $OUTPUT" >&2
  exit 1
fi

echo "Installing dependencies with npm ci..."
npm ci

echo "Checking for known vulnerabilities..."
npm audit

echo "Verifying registry signatures..."
npm audit signatures

echo "Setting up SCSS build..."
mkdir -p scss css

cp custom.scss scss/custom.scss
cp reboot-shim.scss scss/reboot-shim.scss

echo "Compiling SCSS to CSS..."
sass -q scss/custom.scss css/custom.css

echo "Processing output files..."
mkdir -p "$OUTPUT/css" "$OUTPUT/js/min"

sed 's/custom.css.map/bootstrap.custom.css.map/g' css/custom.css \
  > "$OUTPUT/css/bootstrap.custom.css"

sed 's/custom.css/bootstrap.custom.css/g' css/custom.css.map \
  > "$OUTPUT/css/bootstrap.custom.css.map"

cp node_modules/bootstrap/dist/js/bootstrap.bundle.min.js \
  "$OUTPUT/js/min/bootstrap.bundle.min.js"

cp node_modules/bootstrap/dist/js/bootstrap.bundle.min.js.map \
  "$OUTPUT/js/min/bootstrap.bundle.min.js.map"

cp node_modules/mermaid/dist/mermaid.min.js \
  "$OUTPUT/js/min/mermaid.min.js"

echo "Cleaning up intermediate files..."
rm -rf css node_modules scss

echo "Bootstrap assets built successfully"

#!/bin/sh
# Fail if syft emits a CycloneDX spec version the bundled CLI cannot validate.

set -eu

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

printf 'placeholder\n' > "$work/placeholder.txt"
syft "$work" -o cyclonedx-json > "$work/sbom.cdx.json" 2>/dev/null

spec="$(sed -n 's/.*"specVersion"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$work/sbom.cdx.json" | head -n1)"
printf 'syft emits CycloneDX spec %s\n' "$spec"

if out="$(cyclonedx validate --fail-on-errors --input-format json --input-file "$work/sbom.cdx.json" 2>&1)"
then
  printf 'PASS: CDX CLI validates syft output - both agree on spec %s\n' "$spec"
else
  printf 'FAIL: CDX CLI cannot validate syft output spec %s\n%s\n' "$spec" "$out" >&2
  exit 1
fi

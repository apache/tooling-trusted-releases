#!/bin/sh
set -eu
find atr -type f -name '*.py' -exec sh scripts/fix_order.sh {} \;
find tests -type f -name '*.py' -exec sh scripts/fix_order.sh {} \;

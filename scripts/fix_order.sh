#!/bin/sh
set -eu

if [ $# -ne 1 ]
then
  echo "Usage: fix_order.sh <filename>" >&2
  exit 2
fi

file="$1"
tmp="$$.tmp.py"
backup="$HOME/.fix_order.backup.py"
script_dir="$(dirname "$0")"

uv run --frozen python3 "$script_dir/fix_order.py" "$file" > "$tmp"
status=$?
if [ $status -ne 0 ]
then
  rm -f "$tmp"
  exit $status
fi

if cmp -s "$file" "$tmp"
then
  rm -f "$tmp"
else
  cp "$file" "$backup"
  diff -u "$file" "$tmp" || :
  mv "$tmp" "$file"
fi

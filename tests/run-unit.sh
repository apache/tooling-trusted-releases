#!/bin/sh
if [ ! -d .venv ]
then
  echo "You must be in the root directory of the project to run this script"
  exit 1
fi
PYTHONPATH=. exec uv run --frozen pytest tests/unit/

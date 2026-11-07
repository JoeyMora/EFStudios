#!/usr/bin/env bash
# Start EF Studios Artist Finder. The first run sets everything up (about a minute).
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Artist Finder needs Python 3.10 or newer."
  echo "Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Artist Finder (about a minute)..."
  "$PY" -m venv .venv
fi
# (Re)install when the app's requirements change.
if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e .
  touch .venv/.installed
fi
[ -f .env ] || cp .env.example .env

echo "Starting Artist Finder at http://127.0.0.1:5005  (close this window or press Ctrl+C to stop)"
exec .venv/bin/artist-email-finder serve "$@"

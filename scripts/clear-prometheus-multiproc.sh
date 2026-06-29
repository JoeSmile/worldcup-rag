#!/usr/bin/env bash
# Clear Prometheus multiprocess metric files before starting uvicorn with --workers N.
set -euo pipefail

if [[ -z "${PROMETHEUS_MULTIPROC_DIR:-}" ]]; then
  echo "PROMETHEUS_MULTIPROC_DIR is not set; nothing to clear." >&2
  exit 0
fi

mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
find "$PROMETHEUS_MULTIPROC_DIR" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
echo "cleared multiproc metrics in $PROMETHEUS_MULTIPROC_DIR"

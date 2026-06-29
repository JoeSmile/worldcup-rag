#!/usr/bin/env bash
# Clear multiproc metric files once before workers start (not per-worker startup).
set -euo pipefail

if [[ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]]; then
  mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
  find "$PROMETHEUS_MULTIPROC_DIR" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
fi

exec "$@"

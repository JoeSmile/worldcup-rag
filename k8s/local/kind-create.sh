#!/usr/bin/env bash
# Create a local kind cluster for worldcup-rag testing.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-worldcup-rag}"

if ! command -v kind >/dev/null 2>&1; then
  echo "install kind: https://kind.sigs.k8s.io/" >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "kind cluster '$CLUSTER_NAME' already exists"
  exit 0
fi

kind create cluster --name "$CLUSTER_NAME"
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

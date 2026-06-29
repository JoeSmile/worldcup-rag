#!/usr/bin/env bash
# Remove local Kubernetes resources for worldcup-rag.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

NAMESPACE="${NAMESPACE:-worldcup-rag}"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    exit 1
  }
}

require kubectl

echo "==> Deleting kustomize resources"
kubectl delete -k k8s/local --ignore-not-found

echo "==> Deleting extra config maps and secrets"
kubectl delete configmap worldcup-rag-app-config grafana-dashboards \
  --namespace "$NAMESPACE" --ignore-not-found
kubectl delete secret worldcup-rag-secrets \
  --namespace "$NAMESPACE" --ignore-not-found

echo "==> Deleting namespace"
kubectl delete namespace "$NAMESPACE" --ignore-not-found

echo "Done."

#!/usr/bin/env bash
# Local Kubernetes deploy for worldcup-rag (kind / minikube / Docker Desktop).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

CLUSTER_NAME="${CLUSTER_NAME:-worldcup-rag}"
IMAGE_TAG="${IMAGE_TAG:-local}"
ENV_FILE="${ENV_FILE:-.env}"
NAMESPACE="${NAMESPACE:-worldcup-rag}"

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    exit 1
  }
}

require docker
require kubectl

if [[ ! -f "$ENV_FILE" ]]; then
  echo "copy .env.example to .env and set API_KEY (and optional secrets)" >&2
  exit 1
fi

if [[ ! -f config.yaml ]]; then
  echo "missing config.yaml in project root" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

if [[ -z "${API_KEY:-}" ]]; then
  echo "API_KEY is required in $ENV_FILE" >&2
  exit 1
fi

PG_PASSWORD="${PG_PASSWORD:-memoryos}"

echo "==> Building image worldcup-rag:${IMAGE_TAG}"
docker build -t "worldcup-rag:${IMAGE_TAG}" .

if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "==> Loading image into kind cluster: $CLUSTER_NAME"
  kind load docker-image "worldcup-rag:${IMAGE_TAG}" --name "$CLUSTER_NAME"
elif command -v minikube >/dev/null 2>&1 && minikube status >/dev/null 2>&1; then
  echo "==> Loading image into minikube"
  minikube image load "worldcup-rag:${IMAGE_TAG}"
else
  echo "==> Using local Docker image (Docker Desktop K8s shares daemon)"
fi

echo "==> Creating namespace, secrets, and config maps"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic worldcup-rag-secrets \
  --namespace "$NAMESPACE" \
  --from-literal=API_KEY="$API_KEY" \
  --from-literal=PG_PASSWORD="$PG_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap worldcup-rag-app-config \
  --namespace "$NAMESPACE" \
  --from-file=config.yaml=config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboards \
  --namespace "$NAMESPACE" \
  --from-file=worldcup-rag.json=monitoring/grafana/dashboards/worldcup-rag.json \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying manifests"
kubectl apply -k k8s/local

wait_rollout() {
  local deployment="$1"
  echo "==> Waiting for deployment/$deployment"
  kubectl rollout status "deployment/$deployment" -n "$NAMESPACE" --timeout=180s
}

wait_rollout worldcup-postgres
wait_rollout worldcup-redis
wait_rollout worldcup-rag-api
wait_rollout worldcup-rag-worker
wait_rollout prometheus
wait_rollout grafana

cat <<EOF

Deployed to namespace $NAMESPACE.

Pods:
  kubectl get pods -n $NAMESPACE -w

Port-forward API:
  kubectl port-forward -n $NAMESPACE svc/worldcup-rag-api 8000:8000

Health:
  curl http://localhost:8000/health
  curl http://localhost:8000/ready

Chat (needs DB schema + API_KEY):
  curl -s http://localhost:8000/chat -H 'Content-Type: application/json' \\
    -d '{"query":"梅西在世界杯进了几个球？"}'

Prometheus:
  kubectl port-forward -n $NAMESPACE svc/prometheus 9090:9090

Grafana (admin / admin):
  kubectl port-forward -n $NAMESPACE svc/grafana 3000:3000

Teardown:
  ./k8s/local/undeploy.sh

EOF

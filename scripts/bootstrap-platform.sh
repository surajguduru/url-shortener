#!/usr/bin/env bash
# One-time cluster setup: kind cluster, ingress, metrics, monitoring, tracing,
# Argo CD and secrets. The application itself is deployed by Argo CD (GitOps)
# or by `make deploy-local` for development.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=shortener

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config deploy/kind/cluster.yaml
fi
kubectl config use-context "kind-$CLUSTER"

helm repo add traefik https://traefik.github.io/charts >/dev/null
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
helm repo update >/dev/null

# Application namespace enforces the "restricted" Pod Security Standard.
kubectl create namespace shortener --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace shortener --overwrite \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted

scripts/create-secrets.sh

helm upgrade --install traefik traefik/traefik --version 42.0.0 \
  -n traefik --create-namespace -f deploy/platform/traefik-values.yaml --wait
helm upgrade --install metrics-server metrics-server/metrics-server --version 3.14.0 \
  -n kube-system -f deploy/platform/metrics-server-values.yaml --wait
helm upgrade --install kps prometheus-community/kube-prometheus-stack --version 92.10.1 \
  -n monitoring --create-namespace -f deploy/platform/kube-prometheus-stack-values.yaml --wait --timeout 10m
kubectl apply -f deploy/platform/jaeger.yaml
helm upgrade --install argocd argo/argo-cd --version 10.10.5 \
  -n argocd --create-namespace -f deploy/platform/argocd-values.yaml --wait --timeout 10m

echo "Platform ready."
